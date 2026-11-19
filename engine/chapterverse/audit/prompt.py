"""The generation prompt (plan §5 stage 7): numbered passages, fixed instructions, two modes.

The model sees the bound windows as passages ``P1`` .. ``Pn``, each with its coordinate, its
heading and its text. A window with a ``highlight`` span is trimmed to that span plus
:data:`CONTEXT_MARGIN_CHARS` characters on each side (snapped to a word boundary), and a cut is
marked with ``…``. Session state and the last turns are optional and are given as bound
coordinates (Slice 7 fills them); they resolve references and are never evidence.

The instructions say: answer only from the passages; cite by passage id as ``[P1]`` before the
full stop; never write a section, regulation or SI number (citations are rendered by the system
from the ids); say plainly when the passages do not answer the question.

Every fixed string that shapes what the model reads (the templates, the mode rules, the margin,
the inject and steer texts the audit loop writes into the context) is part of
:data:`PROMPT_SHA256`, which goes into ``Policy.prompt_sha256``. Changing any of them changes the
hash, and so the policy hash in every receipt.

Size: the prompt is kept lean for Groq's free tier (8,000 tokens a minute). Token counts are
estimated at :data:`CHARS_PER_TOKEN` characters per token (no tokenizer is loaded here); for the
ERA 1996 s.124 fixture premise (six windows, no highlight) the system and user turns are 3,076
characters, about 770 tokens (494 words). Every block request resends it, and every judge call
sends the windows again (the verifier's candidates), so an answer costs several times that.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from legal_rag_verifier.engine import INJECT_TEMPLATE
from legal_rag_verifier.premise import Premise

from chapterverse.contracts import (
    ChatMessage,
    ChatMode,
    Coordinate,
    Window,
    canonical_json,
    sha256_hex,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "CONTEXT_MARGIN_CHARS",
    "ELLIPSIS",
    "INJECT_TEMPLATE",
    "MODE_RULES",
    "PASSAGE_TEMPLATE",
    "PROMPT_SHA256",
    "SOURCE_TEMPLATE",
    "STEER_NOTE",
    "SYSTEM_TEMPLATE",
    "PriorTurn",
    "Prompt",
    "SessionState",
    "build_prompt",
    "estimate_tokens",
    "render_passage",
    "render_source",
    "trim_window_text",
    "window_positions",
]

CONTEXT_MARGIN_CHARS: Final = 300
"""Characters kept on each side of a window's highlight span."""

CHARS_PER_TOKEN: Final = 4.0
"""The ratio used by :func:`estimate_tokens` (English legal prose, Qwen tokenizer, estimated)."""

ELLIPSIS: Final = "…"

SYSTEM_TEMPLATE: Final = (
    "You answer questions about UK legislation using only the numbered passages below.\n"
    "- Use only the passages, never your own knowledge.\n"
    "- End each sentence that states something from a passage with the ids of the passages it "
    'comes from, before the full stop: "... four weeks [P1]." or "... [P1, P3]."\n'
    "- Never write a section, subsection, regulation, article or SI number, or any other "
    "citation. Citations are added for you from the passage ids.\n"
    "- State figures, dates and conditions exactly as the passages give them.\n"
    "- If the passages do not answer the question, say so plainly in one sentence.\n"
    "- {mode_rule}\n\n"
    "{context}"
    "Passages ({ellipsis} marks text left out):\n\n"
    "{passages}"
)

MODE_RULES: Final[Mapping[ChatMode, str]] = {
    "short": "Answer in a few plain sentences, without headings or lists.",
    "synthesis": (
        "Answer in one or two plain paragraphs that bring the relevant passages together, "
        "without headings or lists."
    ),
}

PASSAGE_TEMPLATE: Final = "[{passage_id}] {coordinate}\n{heading}\n{text}"
CONTEXT_TEMPLATE: Final = (
    "Earlier in this conversation (to resolve references only; it is not evidence):\n{lines}\n\n"
)
SESSION_TEMPLATE: Final = "- Session: {items}"
TURN_TEMPLATE: Final = "- Earlier question: {question} (bound to {coordinates})"
TURN_BARE_TEMPLATE: Final = "- Earlier question bound to {coordinates}"

SOURCE_TEMPLATE: Final = "[{passage_id}] {text}"
"""How one window is written into the context by inject steering, inside ``INJECT_TEMPLATE``."""

STEER_NOTE: Final = (
    "\n\n(Cite only the passage ids listed, as [P1]. Never write a section, regulation or SI "
    "number.)\n\n"
)
"""Written into the context once per answer after a sentence fails the passage-reference rules."""

_TEMPLATE_PARTS: Final = {
    "system": SYSTEM_TEMPLATE,
    "modes": dict(MODE_RULES),
    "passage": PASSAGE_TEMPLATE,
    "context": CONTEXT_TEMPLATE,
    "session": SESSION_TEMPLATE,
    "turn": TURN_TEMPLATE,
    "turn_bare": TURN_BARE_TEMPLATE,
    "source": SOURCE_TEMPLATE,
    "inject": INJECT_TEMPLATE,
    "steer": STEER_NOTE,
    "ellipsis": ELLIPSIS,
    "margin": CONTEXT_MARGIN_CHARS,
}

PROMPT_SHA256: Final = sha256_hex(canonical_json(_TEMPLATE_PARTS))
"""sha256 hex of the canonical JSON of every fixed prompt string. Goes in ``Policy``."""

_PASSAGE_ID = re.compile(r"P[1-9]\d*")


@dataclass(frozen=True, slots=True)
class SessionState:
    """Tier 2 legal session state (plan §5, memory), as bound coordinates. All optional."""

    instrument_focus: Coordinate | None = None
    provision_focus: Coordinate | None = None
    as_at: str | None = None
    extent: str | None = None
    last_bound: tuple[Coordinate, ...] = ()


@dataclass(frozen=True, slots=True)
class PriorTurn:
    """One earlier turn: the coordinates it bound, and its question text when Slice 7 keeps it."""

    coordinates: tuple[Coordinate, ...]
    question: str | None = None


@dataclass(frozen=True, slots=True)
class Prompt:
    """The rendered chat for one answer. ``passage_ids`` are the ids the model may cite."""

    messages: tuple[ChatMessage, ...]
    passage_ids: tuple[str, ...]
    mode: ChatMode

    @property
    def chars(self) -> int:
        return sum(len(m["content"]) for m in self.messages)

    @property
    def estimated_tokens(self) -> int:
        """Estimated prompt tokens (:data:`CHARS_PER_TOKEN`), message framing not counted."""
        return estimate_tokens("".join(m["content"] for m in self.messages))


def estimate_tokens(text: str) -> int:
    """Tokens in ``text`` at :data:`CHARS_PER_TOKEN` characters a token, rounded up."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def trim_window_text(
    text: str, highlight: tuple[int, int] | None, margin: int = CONTEXT_MARGIN_CHARS
) -> str:
    """``text`` cut to ``highlight`` plus ``margin`` characters each side, cuts marked ``…``.

    A cut never splits a word: it moves inward to the nearest space within the margin. No
    highlight, or an empty or out-of-range one, keeps the whole text.
    """
    if highlight is None:
        return text
    start, end = max(0, highlight[0]), min(len(text), highlight[1])
    if start >= end:
        return text
    a, b = max(0, start - margin), min(len(text), end + margin)
    if a > 0:
        space = text.find(" ", a, start)
        a = space + 1 if space != -1 else a
    if b < len(text):
        space = text.rfind(" ", end, b)
        b = space if space != -1 else b
    body = text[a:b].strip()
    head = f"{ELLIPSIS} " if a > 0 else ""
    tail = f" {ELLIPSIS}" if b < len(text) else ""
    return head + body + tail


def render_passage(window: Window) -> str:
    """One passage as the model sees it: id, coordinate, heading, (trimmed) text."""
    return PASSAGE_TEMPLATE.format(
        passage_id=window.passage_id,
        coordinate=window.coordinate,
        heading=window.heading,
        text=trim_window_text(window.text, window.highlight),
    )


def render_source(windows: Sequence[Window]) -> str:
    """Windows written into the context by inject steering: id and full (untrimmed) text."""
    return "\n\n".join(
        SOURCE_TEMPLATE.format(passage_id=w.passage_id, text=w.text) for w in windows
    )


def window_positions(premise: Premise, windows: Sequence[Window]) -> dict[int, Window]:
    """Premise passage position -> the prompt window with the same coordinate.

    The verifier reports aligned windows as premise positions; this is how a position becomes a
    passage id. Positions with no prompt window (not shown to the model) are absent.
    """
    by_coordinate = {w.coordinate: w for w in windows}
    out: dict[int, Window] = {}
    for i, passage in enumerate(premise.passages):
        if passage.coordinate is not None and passage.coordinate in by_coordinate:
            out[i] = by_coordinate[passage.coordinate]
    return out


def _context(session: SessionState | None, history: Sequence[PriorTurn]) -> str:
    lines: list[str] = []
    for turn in history:
        coordinates = ", ".join(turn.coordinates) or "nothing"
        if turn.question:
            lines.append(TURN_TEMPLATE.format(question=turn.question, coordinates=coordinates))
        else:
            lines.append(TURN_BARE_TEMPLATE.format(coordinates=coordinates))
    if session is not None:
        items = [
            f"{name} {value}"
            for name, value in (
                ("instrument", session.instrument_focus),
                ("provision", session.provision_focus),
                ("as at", session.as_at),
                ("extent", session.extent),
                ("last bound", ", ".join(session.last_bound) or None),
            )
            if value
        ]
        if items:
            lines.append(SESSION_TEMPLATE.format(items="; ".join(items)))
    return CONTEXT_TEMPLATE.format(lines="\n".join(lines)) if lines else ""


def build_prompt(
    query: str,
    windows: Sequence[Window],
    *,
    mode: ChatMode = "short",
    session: SessionState | None = None,
    history: Sequence[PriorTurn] = (),
) -> Prompt:
    """The system turn (instructions, optional context, passages) and the question.

    Raises:
        ValueError: no windows, a passage id not of the form ``P<n>``, a repeated passage id,
            or an unknown mode.
    """
    if mode not in MODE_RULES:
        raise ValueError(f"unknown mode {mode!r}")
    if not windows:
        raise ValueError("a prompt needs at least one window")
    ids = tuple(w.passage_id for w in windows)
    bad = [i for i in ids if not _PASSAGE_ID.fullmatch(i)]
    if bad:
        raise ValueError(f"passage ids must look like P1, not {bad[0]!r}")
    if len(set(ids)) != len(ids):
        raise ValueError("passage ids must be unique")
    system = SYSTEM_TEMPLATE.format(
        mode_rule=MODE_RULES[mode],
        context=_context(session, history),
        ellipsis=ELLIPSIS,
        passages="\n\n".join(render_passage(w) for w in windows),
    )
    messages = (
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=query),
    )
    return Prompt(messages=messages, passage_ids=ids, mode=mode)
