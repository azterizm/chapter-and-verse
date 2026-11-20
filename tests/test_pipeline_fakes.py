"""Fakes shared by the pipeline and app tests (agent C1). Holds no tests.

* :class:`FakeRotation`: a ``Rotation`` that answers generation requests from a script and judge
  requests by rules, in the judge's reply format (``{"label": ..., "passage": n}``).
* :class:`CharBackend`: a character-level verifier ``Backend`` for the Modal KV path.
* :func:`parse_sse`: split a response body into ``(type, data, raw data line)``.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from legal_rag_verifier.engine import Constraint, Segment, Tokens

from chapterverse.contracts import (
    ChatRequest,
    ChatResult,
    FinishReason,
    ProviderUnavailable,
    canonical_json,
)

S124_QUERY = "What is the limit of the compensatory award under s.124 ERA 1996?"
MARCHWOOD_QUERY = "What does the Marchwood Commercial Arbitration Act 1996 say about appeals?"
TITLE_ONLY_QUERY = "What does the National Health Service (Invalid Direction) Act 1980 say?"
AMBIGUOUS_QUERY = "What does section 124 say?"

GOOD1 = (
    "The compensatory award cannot exceed the lower of £123,543 and 52 multiplied by a "
    "week\u2019s pay [P2]."
)
GOOD2 = "The limit does not apply in some unfair dismissal cases [P3]."
WRONG = "The compensatory award cannot exceed £105,707 [P2]."
CITED = "Under subsection (1ZA) the award cannot exceed £123,543 [P2]."

#: (substring of the sentence, label, text that marks the passage that decides it)
RULES: tuple[tuple[str, str, str], ...] = (
    ("105,707", "contradicted", "£123,543"),
    ("£123,543", "supported", "£123,543"),
    ("does not apply", "supported", "Subsection (1) shall not apply"),
)

Script = Sequence[tuple[str, FinishReason] | Exception]


class FakeRotation:
    """``Rotation``: scripted generation blocks, rule-labelled judge replies. Thread-safe.

    ``provider`` names who served each call. ``gate`` (when set) makes every generation call
    after the first wait for it, so a test can hold the worker between sentences.
    """

    def __init__(
        self,
        script: Script = (),
        *,
        rules: Sequence[tuple[str, str, str]] = RULES,
        provider: str = "groq",
        judge_error: Exception | None = None,
        names: Sequence[str] = ("groq",),
    ) -> None:
        self.script = list(script)
        self.rules = rules
        self.provider = provider
        self.judge_error = judge_error
        self.names = list(names)
        self.requests: list[ChatRequest] = []
        self.gate: threading.Event | None = None
        self._lock = threading.Lock()

    def available(self, purpose: str | None = None) -> list[str]:
        del purpose
        return list(self.names)

    @property
    def generate_requests(self) -> list[ChatRequest]:
        return [r for r in self.requests if r.purpose == "generate"]

    def complete(self, req: ChatRequest) -> ChatResult:
        with self._lock:
            self.requests.append(req)
            first = len(self.generate_requests) == 1
        if req.purpose == "judge":
            if self.judge_error is not None:
                raise self.judge_error
            return self._judge(req)
        if self.gate is not None and not first:
            self.gate.wait(10)
        with self._lock:
            if not self.script:
                raise AssertionError("more generation blocks asked for than scripted")
            item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        text, finish = item
        return ChatResult(text, finish, self.provider, "qwen/qwen3.8-27b", None, 900, 60, 12.5)

    def _judge(self, req: ChatRequest) -> ChatResult:
        user = req.messages[1]["content"]
        sentence = user.split("Sentence to check:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
        label, marker = "supported", ""
        for needle, rule_label, rule_marker in self.rules:
            if needle in sentence:
                label, marker = rule_label, rule_marker
                break
        candidates = re.findall(r"^\[(\d+)\] (.*)$", user, re.MULTILINE)
        passage = next((int(n) for n, text in candidates if marker and marker in text), 0)
        content = json.dumps({"label": label, "passage": passage})
        return ChatResult(content, "stop", self.provider, "judge-model", "fp8", 400, 9, 3.0)


def unavailable(*names: str) -> Exception:
    from chapterverse.contracts import AllProvidersUnavailable  # noqa: PLC0415

    return AllProvidersUnavailable(
        [ProviderUnavailable(n, "rate_limit", 30.0, "HTTP 429") for n in names or ("groq",)]
    )


class CharBackend:
    """A character-level verifier ``Backend``: one token per character.

    ``stream`` maps a context (the text after the prompt where a draft starts) to what the model
    writes there up to EOS.
    """

    name = "fake"
    model_id = "fake-qwen"

    def __init__(self, stream: Mapping[str, str]) -> None:
        self.stream = dict(stream)
        self.prompt_len = 0

    def chat(self, messages: Sequence[Mapping[str, str]]) -> Tokens:
        tokens = self.encode("\n".join(m["content"] for m in messages) + "\n<assistant>\n")
        self.prompt_len = len(tokens)
        return tokens

    def encode(self, text: str) -> Tokens:
        return tuple(ord(c) for c in text)

    def decode(self, tokens: Sequence[int]) -> str:
        return "".join(chr(t) for t in tokens)

    def _rest(self, context: str) -> str:
        best, found = "", False
        for base, text in self.stream.items():
            fits = context.startswith(base) and (base + text).startswith(context)
            if fits and len(base) >= len(best):
                best, found = base, True
        return (best + self.stream[best])[len(context) :] if found else ""

    def extend(
        self,
        committed: Sequence[int],
        *,
        stop: Sequence[str],
        max_new: int,
        constraint: Constraint | None,
    ) -> Segment:
        del constraint
        rest = self._rest(self.decode(committed[self.prompt_len :]))
        if not rest:
            return Segment((), "", "eos", len(committed), 1000)
        cut, finish = len(rest), "eos"
        for s in stop:
            i = rest.find(s)
            if i != -1 and i + len(s) <= cut:
                cut, finish = i + len(s), "stop"
        if cut > max_new:
            cut, finish = max_new, "length"
        piece = rest[:cut]
        return Segment(self.encode(piece), piece, finish, len(committed), 1000)  # type: ignore[arg-type]


def parse_sse(body: bytes) -> list[tuple[str, dict[str, Any], str]]:
    """``(event type, data, raw data line)`` per event; checks each is in canonical form."""
    out: list[tuple[str, dict[str, Any], str]] = []
    assert body.endswith(b"\n\n")
    for block in body[:-2].split(b"\n\n"):
        head, data_line = block.split(b"\n", 1)
        assert head.startswith(b"event: ")
        assert data_line.startswith(b"data: ")
        kind = head[len(b"event: ") :].decode("ascii")
        raw = data_line[len(b"data: ") :].decode("utf-8")
        data = json.loads(raw)
        assert data["type"] == kind
        # encode_sse writes canonical JSON: re-encoding the parsed data gives the same bytes.
        assert canonical_json(data).decode("utf-8") == raw
        out.append((kind, data, raw))
    return out


def fact_bytes(raw_receipt: str) -> bytes:
    """The fact object's bytes exactly as serialised in a receipt data line (web client rule)."""
    prefix, suffix = ',"fact":', ',"type":"receipt"}'
    assert raw_receipt.startswith('{"answer_id":')
    assert raw_receipt.endswith(suffix)
    at = raw_receipt.index(prefix)
    return raw_receipt[at + len(prefix) : len(raw_receipt) - len(suffix)].encode("utf-8")


Emit = Callable[[Any], None]
