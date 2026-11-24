"""Verified generation: the chat block pattern and the Modal engine path (agent B4).

Every provider is a fake: a scripted ``Rotation``, a ``JudgeChat`` that labels each sentence by
rules in the judge's reply format, and a character-level verifier ``Backend``. The premise is the
fixture ERA 1996 s.124; its windows are P1 (1), P2 (1ZA), P3 (1A), P4 (3), P5 (4), P6 (5).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import pytest
from legal_rag_verifier.engine import INJECT_TEMPLATE, Constraint, Segment, Tokens
from legal_rag_verifier.premise import Premise

from chapterverse.audit.continuation import (
    ChatContinuationGenerator,
    GenerationSettings,
    split_block,
)
from chapterverse.audit.modal_path import (
    BearerPost,
    ModalPathGenerator,
    ModalSGLangBackend,
    _can_ban,
)
from chapterverse.audit.policy import ABSTAIN_TEXT, build_policy
from chapterverse.audit.prompt import STEER_NOTE
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ChatRequest,
    ChatResult,
    FinishReason,
    JudgeReply,
    Policy,
    ProviderUnavailable,
    SentenceOut,
    TemporalStatus,
    TextStore,
    Window,
)

S124 = "uk/ukpga/1996/18/s124"
QUERY = "What is the maximum compensatory award for unfair dismissal?"
REFUSAL = ABSTAIN_TEXT

GOOD1 = (
    "The compensatory award cannot exceed the lower of £123,543 and 52 multiplied by a "
    "week\u2019s pay [P2]."
)
GOOD2 = "The limit does not apply in some unfair dismissal cases [P3]."
WRONG = "The compensatory award cannot exceed £105,707 [P2]."
CITED = "Under section 98 the award cannot exceed £123,543 [P2]."  # s.98: not P2, not in its text
UNKNOWN = GOOD1.replace("[P2]", "[P9]")
TEXT1 = GOOD1.replace(" [P2]", "")
TEXT2 = GOOD2.replace(" [P3]", "")

#: (substring of the sentence, label, text that marks the passage that decides it)
RULES = (
    ("105,707", "contradicted", "£123,543"),
    ("£123,543", "supported", "£123,543"),
    ("does not apply", "supported", "Subsection (1) shall not apply"),
)


# ---------------------------------------------------------------------- fakes


class FakeRotation:
    """Returns scripted blocks in order; an exception in the script is raised."""

    def __init__(self, script: Sequence[tuple[str, FinishReason] | Exception]) -> None:
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    def complete(self, req: ChatRequest) -> ChatResult:
        self.requests.append(req)
        if not self.script:
            raise AssertionError("the generator asked for more blocks than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        text, finish = item
        return ChatResult(text, finish, "groq", "qwen/qwen3.8-27b", None, 900, 60, 12.5)

    def available(self) -> list[str]:
        return ["groq"]


class FakeJudgeChat:
    """Labels a sentence by the first matching rule (default supported, passage 0)."""

    model = "fake-judge"

    def __init__(
        self, rules: Sequence[tuple[str, str, str]] = RULES, *, fail_on: str | None = None
    ) -> None:
        self.rules = rules
        self.fail_on = fail_on
        self.sentences: list[str] = []

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        user = messages[1]["content"]
        sentence = user.split("Sentence to check:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
        self.sentences.append(sentence)
        if self.fail_on is not None and self.fail_on in sentence:
            raise AllProvidersUnavailable([ProviderUnavailable("groq", "quota")])
        label, marker = "supported", ""
        for needle, rule_label, rule_marker in self.rules:
            if needle in sentence:
                label, marker = rule_label, rule_marker
                break
        candidates = re.findall(r"^\[(\d+)\] (.*)$", user, re.MULTILINE)
        passage = next((int(n) for n, text in candidates if marker and marker in text), 0)
        content = json.dumps({"label": label, "passage": passage})
        return JudgeReply(content=content, ms=3.0, tokens=7, finish="stop")


class FakeBackend:
    """A character-level verifier ``Backend``: one token per character.

    ``stream`` maps a context (the text after the prompt where a draft starts) to what the model
    writes there up to EOS. ``extend`` finds the entry the current context continues and returns
    its next piece, up to and including the first stop string.
    """

    name = "fake"
    model_id = "fake-qwen"

    def __init__(self, stream: Mapping[str, str]) -> None:
        self.stream = dict(stream)
        self.prompt_len = 0
        self.constraints: list[Constraint | None] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> Tokens:
        tokens = self.encode("\n".join(m["content"] for m in messages) + "\n<assistant>\n")
        self.prompt_len = len(tokens)
        return tokens

    def encode(self, text: str) -> Tokens:
        return tuple(ord(c) for c in text)

    def decode(self, tokens: Sequence[int]) -> str:
        return "".join(chr(t) for t in tokens)

    def _rest(self, context: str) -> str:
        best = ""
        found = False
        for base, text in self.stream.items():
            full = base + text
            if context.startswith(base) and full.startswith(context) and len(base) >= len(best):
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
        self.constraints.append(constraint)
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


# ---------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def premise(fixture_text_store: TextStore) -> Premise:
    return Premise.from_records(fixture_text_store.get(S124))


@pytest.fixture(scope="module")
def windows(premise: Premise) -> tuple[Window, ...]:
    return tuple(
        Window(
            passage_id=f"P{i}",
            coordinate=str(p.coordinate),
            heading=p.heading or "",
            text=p.text,
            status=TemporalStatus.IN_FORCE,
            version_date="2026-09-28",
            source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/124",
        )
        for i, p in enumerate(premise.passages, 1)
    )


@pytest.fixture(scope="module")
def policy() -> Policy:
    return build_policy("qwen27-ar4")


def inject(windows: Sequence[Window], *ids: int) -> str:
    source = "\n\n".join(f"[P{i}] {windows[i - 1].text}" for i in ids)
    return INJECT_TEMPLATE.format(source=source)


Run = tuple[list[SentenceOut], FakeRotation, FakeJudgeChat, ChatContinuationGenerator]


@pytest.fixture
def run_chat(premise: Premise, windows: tuple[Window, ...], policy: Policy) -> Callable[..., Run]:
    def run(
        script: Sequence[tuple[str, FinishReason] | Exception],
        *,
        judge: FakeJudgeChat | None = None,
        **kwargs: Any,
    ) -> Run:
        rotation = FakeRotation(script)
        judge = judge or FakeJudgeChat()
        gen = ChatContinuationGenerator(
            rotation, judge, policy, query=QUERY, premise=premise, windows=windows, **kwargs
        )
        out = list(gen)
        assert not rotation.script, "scripted blocks left over"
        return out, rotation, judge, gen

    return run


def supported(index: int, text: str, ids: tuple[str, ...], rollbacks: int = 0) -> SentenceOut:
    return SentenceOut(index, text, "supported", ids, ("GROUNDED",), rollbacks)


# ---------------------------------------------------------------------- the block pattern


def test_every_sentence_passes(run_chat: Callable[..., Run]) -> None:
    out, rotation, judge, gen = run_chat([(f"{GOOD1} {GOOD2}", "stop")])
    assert out == [supported(0, TEXT1, ("P2",)), supported(1, TEXT2, ("P3",))]
    assert gen.stop_reason == "stop"
    (request,) = rotation.requests
    assert request.prefill is None
    assert (request.max_tokens, request.mode, request.seed) == (200, "short", 0)
    assert (request.temperature, request.top_p, request.purpose) == (0.7, 0.8, "generate")
    assert request.messages == gen.prompt.messages
    assert judge.sentences == [TEXT1, TEXT2]  # verified with the references stripped
    assert [c.purpose for c in gen.calls] == ["generate", "judge", "judge"]
    assert gen.calls[0].provider == "groq"
    assert gen.calls[1].model_id == "fake-judge"
    assert gen.calls[1].completion_tokens == 7


def test_one_wrong_figure_is_repaired_by_inject(
    run_chat: Callable[..., Run], windows: tuple[Window, ...]
) -> None:
    out, rotation, judge, gen = run_chat(
        [(f"{WRONG} {GOOD2}", "stop"), (f"{GOOD1} {GOOD2}", "stop")]
    )
    assert out == [supported(0, TEXT1, ("P2",), rollbacks=1), supported(1, TEXT2, ("P3",))]
    assert rotation.requests[1].prefill == inject(windows, 2)  # the window the judge named
    assert rotation.requests[1].seed == 0
    assert judge.sentences == [WRONG.replace(" [P2]", ""), TEXT1, TEXT2]  # rest discarded
    assert gen.stop_reason == "stop"


def test_three_failed_retries_give_the_refusal_sentence(
    run_chat: Callable[..., Run], windows: tuple[Window, ...]
) -> None:
    wrong = (f"{WRONG} {GOOD2}", "stop")
    out, rotation, _, gen = run_chat([wrong, wrong, wrong, wrong, (GOOD2, "stop")])
    assert out == [
        SentenceOut(0, REFUSAL, "refused", (), ("NLI_CONTRADICTION",), 4),
        supported(1, TEXT2, ("P3",)),
    ]
    source = inject(windows, 2)
    assert [r.prefill for r in rotation.requests] == [None, source, source, source,
                                                      source + REFUSAL]  # fmt: skip
    # inject once, then resample (P2 already injected), seed moving on each time
    assert [r.seed for r in rotation.requests] == [0, 0, 1, 2, 2]
    assert gen.stop_reason == "stop"


def test_the_answer_budget_ends_the_answer(run_chat: Callable[..., Run]) -> None:
    wrong = (WRONG, "stop")
    out, rotation, _, gen = run_chat([wrong] * 8)
    assert len(out) == 1
    assert out[0] == SentenceOut(0, REFUSAL, "refused", (), ("NLI_CONTRADICTION",), 4)
    assert gen.stop_reason == "abstained"
    assert len(rotation.requests) == 8


def test_narration_sentence_that_passed_judge_becomes_abstain_text(
    run_chat: Callable[..., Run],
) -> None:
    narration_sentence = "The provided passages do not state the general limitation period [P1]."
    out, _rotation, judge, gen = run_chat([(narration_sentence, "stop")])
    assert len(out) == 1
    assert out[0] == SentenceOut(
        0,
        ABSTAIN_TEXT,
        "refused",
        (),
        ("NARRATION",),
        0,
    )
    assert judge.sentences == []
    assert [c for c in gen.calls if c.purpose == "judge"] == []
    assert gen.stop_reason == "abstained"


@pytest.mark.parametrize(
    "drafted",
    [
        ABSTAIN_TEXT,
        f"{ABSTAIN_TEXT} [P1]",
        f"{ABSTAIN_TEXT[:-1]} [P1].",
        ABSTAIN_TEXT.upper(),
        ABSTAIN_TEXT.lower(),
        ABSTAIN_TEXT[:-1],
    ],
)
def test_chat_drafted_abstention_gives_one_refusal_and_zero_judge_calls(
    run_chat: Callable[..., Run], drafted: str
) -> None:
    out, _rotation, judge, gen = run_chat([(drafted, "stop")])
    assert out == [SentenceOut(0, ABSTAIN_TEXT, "refused", (), ("ABSTAINED",), 0)]
    assert judge.sentences == []
    assert [c for c in gen.calls if c.purpose == "judge"] == []
    assert gen.stop_reason == "abstained"


def test_two_refusing_points_give_one_abstain_text(run_chat: Callable[..., Run]) -> None:
    wrong = (WRONG, "stop")
    out, _, _, gen = run_chat([wrong] * 8)
    assert len(out) == 1
    assert out[0] == SentenceOut(0, REFUSAL, "refused", (), ("NLI_CONTRADICTION",), 4)
    assert gen.stop_reason == "abstained"


def test_a_citation_written_by_the_model_fails_without_a_judge_call(
    run_chat: Callable[..., Run],
) -> None:
    out, rotation, judge, _ = run_chat([(CITED, "stop"), (GOOD1, "stop")])
    assert out == [supported(0, TEXT1, ("P2",), rollbacks=1)]
    assert judge.sentences == [TEXT1]
    assert rotation.requests[1].prefill == STEER_NOTE


def test_a_model_citation_every_time_is_refused_with_its_reason(
    run_chat: Callable[..., Run],
) -> None:
    out, rotation, judge, _ = run_chat([(CITED, "stop")] * 4 + [("", "stop")])
    assert out == [
        SentenceOut(0, REFUSAL, "refused", (), ("UNGROUNDED_CITATION", "MODEL_CITATION"), 4)
    ]
    assert judge.sentences == []
    assert [r.seed for r in rotation.requests] == [0, 0, 1, 2, 2]  # note once, then resample
    assert all("124(1ZA)" not in s.text for s in out)


def test_an_unknown_passage_id_fails(run_chat: Callable[..., Run]) -> None:
    out, _, judge, _ = run_chat([(UNKNOWN, "stop")] * 4 + [(GOOD2, "stop")])
    assert out[0] == SentenceOut(
        0, REFUSAL, "refused", (), ("UNGROUNDED_CITATION", "UNKNOWN_PASSAGE"), 4
    )
    assert out[1] == supported(1, TEXT2, ("P3",))
    assert judge.sentences == [TEXT2]


def test_every_prefill_ends_at_a_sentence_end(
    run_chat: Callable[..., Run], windows: tuple[Window, ...]
) -> None:
    script: list[tuple[str, FinishReason]] = [
        (f"{TEXT1} [P2] The limit does not apply in so", "length"),  # reference after the stop
        ("The limit is the lower of", "length"),  # no complete sentence
        (f"{WRONG} The", "length"),
        (f"{GOOD2} It also", "length"),
        ("", "stop"),
    ]
    out, rotation, _, _ = run_chat(script)
    assert out == [supported(0, TEXT1, ("P2",)), supported(1, TEXT2, ("P3",), rollbacks=2)]
    prefills = [r.prefill for r in rotation.requests]
    assert prefills[1] == GOOD1  # the reference moved before the full stop
    assert prefills[3] == GOOD1 + inject(windows, 2)
    assert prefills[4] == prefills[3] + GOOD2
    for prefill in prefills[1:]:
        assert prefill is not None
        assert prefill.endswith((".", "\n"))
    assert [r.seed for r in rotation.requests] == [0, 0, 1, 1, 1]


def test_providers_unavailable_mid_answer_propagates_after_sentences(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    outage = AllProvidersUnavailable([ProviderUnavailable("groq", "rate_limit", 20.0)])
    rotation = FakeRotation([(f"{GOOD1} The limit", "length"), outage])
    gen = ChatContinuationGenerator(
        rotation, FakeJudgeChat(), policy, query=QUERY, premise=premise, windows=windows
    )
    stream = iter(gen)
    assert next(stream) == supported(0, TEXT1, ("P2",))
    with pytest.raises(AllProvidersUnavailable) as raised:
        next(stream)
    assert raised.value is outage
    assert gen.stop_reason is None
    assert [c.purpose for c in gen.calls] == ["generate", "judge"]


def test_judge_outage_mid_answer_propagates_after_sentences(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    judge = FakeJudgeChat(fail_on="does not apply")
    gen = ChatContinuationGenerator(
        FakeRotation([(f"{GOOD1} {GOOD2}", "stop")]),
        judge,
        policy,
        query=QUERY,
        premise=premise,
        windows=windows,
    )
    seen: list[SentenceOut] = []
    with pytest.raises(AllProvidersUnavailable):
        seen.extend(gen)
    assert seen == [supported(0, TEXT1, ("P2",))]


def test_identical_inputs_give_identical_outputs(run_chat: Callable[..., Run]) -> None:
    def script() -> list[tuple[str, FinishReason]]:
        wrong: tuple[str, FinishReason] = (f"{WRONG} {GOOD2}", "stop")
        return [wrong, (CITED, "stop"), wrong, wrong, (f"{GOOD2} {TEXT1}", "length"),
                ("", "stop")]  # fmt: skip

    first = run_chat(script())
    second = run_chat(script())
    assert first[0] == second[0]
    assert first[1].requests == second[1].requests
    assert first[2].sentences == second[2].sentences
    assert first[3].calls == second[3].calls
    assert first[0][0].verdict == "refused"


def test_synthesis_thinks_on_the_first_block_only(run_chat: Callable[..., Run]) -> None:
    _, rotation, _, _ = run_chat([(f"{GOOD1} The", "length"), (GOOD2, "stop")], mode="synthesis")
    first, second = rotation.requests
    assert (first.mode, first.max_tokens, first.temperature, first.top_p) == (
        "synthesis", 2248, 1.0, 0.95
    )  # fmt: skip
    assert (second.mode, second.max_tokens, second.temperature) == ("short", 200, 0.7)


def test_answer_length_and_provider_stops(run_chat: Callable[..., Run]) -> None:
    out, _, _, gen = run_chat(
        [(f"{GOOD1} {GOOD2}", "stop")], settings=GenerationSettings(max_answer_tokens=10)
    )
    assert len(out) == 1
    assert gen.stop_reason == "length"
    out, _, _, gen = run_chat([(f"{GOOD1} The limit", "content_filter")])
    assert len(out) == 1
    assert gen.stop_reason == "content_filter"


def test_a_claim_free_sentence_needs_no_reference(run_chat: Callable[..., Run]) -> None:
    out, _, _, _ = run_chat([(f"Yes. {GOOD1}", "stop")])
    assert out[0] == SentenceOut(0, "Yes.", "supported", (), ("GROUNDED",), 0)
    assert out[1] == supported(1, TEXT1, ("P2",))


def test_a_generator_answers_once(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    gen = ChatContinuationGenerator(
        FakeRotation([("", "stop")]), FakeJudgeChat(), policy,
        query=QUERY, premise=premise, windows=windows,
    )  # fmt: skip
    assert list(gen) == []
    with pytest.raises(RuntimeError, match="once"):
        iter(gen)


@pytest.mark.parametrize(
    ("text", "final", "sentences", "complete"),
    [
        ("A [P1]. B [P2].", True, ["A [P1].", "B [P2]."], True),
        ("A. [P1] B [P2].", False, ["A [P1]."], False),  # trailing "." undecided mid-stream
        ("A [P1]. B is", False, ["A [P1]."], False),
        ("A [P1]. [P", False, [], False),  # the reference is cut off
        ("[P1] A [P2].", True, ["A [P2]."], True),  # leading references dropped
        ("A [P1]. B", True, ["A [P1].", "B"], True),
    ],
)
def test_split_block(text: str, final: bool, sentences: list[str], complete: bool) -> None:
    assert split_block(text, final=final) == (sentences, complete)


# ---------------------------------------------------------------------- the Modal path


def modal(
    stream: Mapping[str, str],
    premise: Premise,
    windows: Sequence[Window],
    policy: Policy,
    judge: FakeJudgeChat | None = None,
) -> tuple[ModalPathGenerator, FakeBackend, FakeJudgeChat, list[SentenceOut]]:
    backend = FakeBackend(stream)
    judge = judge or FakeJudgeChat()
    streamed: list[SentenceOut] = []
    gen = ModalPathGenerator(
        backend, judge, policy, query=QUERY, premise=premise, windows=windows,
        on_sentence=streamed.append,
    )  # fmt: skip
    return gen, backend, judge, streamed


def test_modal_every_sentence_passes(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    gen, _, judge, streamed = modal({"": f"{GOOD1} {GOOD2}"}, premise, windows, policy)
    out = list(gen)
    assert out == [supported(0, TEXT1, ("P2",)), supported(1, TEXT2, ("P3",))]
    assert streamed == out
    assert judge.sentences == [TEXT1, TEXT2]
    assert gen.stop_reason == "eos"
    purposes = {c.purpose for c in gen.calls}
    assert purposes == {"generate", "judge"}
    generate = [c for c in gen.calls if c.purpose == "generate"]
    assert all(c.provider == "modal" and c.model_id == "fake-qwen" for c in generate)
    assert gen.trace is not None
    assert gen.trace["engine"]["steering"] == "inject"
    assert gen.trace["engine"]["max_rollbacks"] == 3


def test_modal_wrong_figure_is_repaired_by_inject(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    source = inject(windows, 2)
    stream = {"": f"{WRONG} {GOOD2}", source: GOOD1, source + GOOD1: f" {GOOD2}"}
    gen, _, _, _ = modal(stream, premise, windows, policy)
    out = list(gen)
    assert out == [supported(0, TEXT1, ("P2",), rollbacks=1), supported(1, TEXT2, ("P3",))]
    assert gen.trace is not None
    first = gen.trace["sentences"][0]
    assert first["recovered_by"] == "inject"
    assert first["attempts"][1]["injected"]["windows"] == [1]


def test_modal_model_citation_is_refused(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    stream = {"": CITED, STEER_NOTE: CITED}
    gen, backend, judge, streamed = modal(stream, premise, windows, policy)
    out = list(gen)
    assert out == [
        SentenceOut(0, REFUSAL, "refused", (), ("UNGROUNDED_CITATION", "MODEL_CITATION"), 4)
    ]
    assert streamed == out
    assert judge.sentences == []
    assert any(c is not None for c in backend.constraints)  # note first, then bans


def test_modal_narration_sentence_becomes_abstain_text(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    narration_sentence = "The provided passages do not state the general limitation period [P1]."
    gen, _, judge, streamed = modal({"": narration_sentence}, premise, windows, policy)
    out = list(gen)
    assert out == [
        SentenceOut(
            0,
            ABSTAIN_TEXT,
            "refused",
            (),
            ("NARRATION",),
            0,
        )
    ]
    assert streamed == out
    assert judge.sentences == []
    assert [c for c in gen.calls if c.purpose == "judge"] == []
    assert gen.stop_reason == "abstained"


@pytest.mark.parametrize(
    "drafted",
    [
        ABSTAIN_TEXT,
        f"{ABSTAIN_TEXT} [P1]",
        f"{ABSTAIN_TEXT[:-1]} [P1].",
        ABSTAIN_TEXT.upper(),
        ABSTAIN_TEXT.lower(),
        ABSTAIN_TEXT[:-1],
    ],
)
def test_modal_drafted_abstention_gives_one_refusal_and_zero_judge_calls(
    premise: Premise, windows: tuple[Window, ...], policy: Policy, drafted: str
) -> None:
    gen, _, judge, streamed = modal({"": drafted}, premise, windows, policy)
    out = list(gen)
    assert out == [SentenceOut(0, ABSTAIN_TEXT, "refused", (), ("ABSTAINED",), 0)]
    assert streamed == out
    assert judge.sentences == []
    assert [c for c in gen.calls if c.purpose == "judge"] == []
    assert gen.stop_reason == "abstained"


def test_modal_two_refusing_points_give_one_abstain_text(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    stream = {
        "": CITED,
        STEER_NOTE: CITED,
        STEER_NOTE + ABSTAIN_TEXT: CITED,
        STEER_NOTE + " " + ABSTAIN_TEXT: CITED,
    }
    gen, _, _, streamed = modal(stream, premise, windows, policy)
    out = list(gen)
    assert len(out) == 1
    assert out[0].verdict == "refused"
    assert out[0].text == ABSTAIN_TEXT
    assert streamed == out
    assert gen.stop_reason == "abstained"


def test_modal_outage_propagates_after_sentences(
    premise: Premise, windows: tuple[Window, ...], policy: Policy
) -> None:
    judge = FakeJudgeChat(fail_on="does not apply")
    gen, _, _, streamed = modal({"": f"{GOOD1} {GOOD2}"}, premise, windows, policy, judge)
    stream = iter(gen)
    assert next(stream) == supported(0, TEXT1, ("P2",))
    with pytest.raises(AllProvidersUnavailable):
        next(stream)
    assert streamed == [supported(0, TEXT1, ("P2",))]


# ---------------------------------------------------------------------- Modal transport


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self, outcome: bytes | Exception) -> None:
        self.outcome = outcome
        self.requests: list[urllib.request.Request] = []

    @contextmanager
    def __call__(self, request: urllib.request.Request, timeout: float) -> Iterator[FakeResponse]:
        del timeout
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        yield FakeResponse(self.outcome)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://m.example/generate", code, "x", None, io.BytesIO())  # type: ignore[arg-type]


def test_bearer_post_reads_the_key_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = FakeOpener(b'{"output_ids": [1], "meta_info": {}}')
    post = BearerPost("https://m.example/", api_key_env="TEST_MODAL_KEY", opener=opener)
    monkeypatch.delenv("TEST_MODAL_KEY", raising=False)
    assert post({"input_ids": [1]}) == {"output_ids": [1], "meta_info": {}}
    assert opener.requests[0].get_header("Authorization") is None
    monkeypatch.setenv("TEST_MODAL_KEY", "  sekret  ")
    post({"input_ids": [1]})
    request = opener.requests[1]
    assert request.full_url == "https://m.example/generate"
    assert request.get_header("Authorization") == "Bearer sekret"
    assert "sekret" not in repr(post)


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (http_error(401), "config"),
        (http_error(429), "rate_limit"),
        (http_error(503), "server"),
        (TimeoutError(), "timeout"),
        (urllib.error.URLError("refused"), "server"),
        (b"[1, 2]", "server"),
        (b"not json", "server"),
    ],
)
def test_bearer_post_failures_are_provider_outages(outcome: bytes | Exception, kind: str) -> None:
    post = BearerPost("https://m.example", api_key_env="UNSET_TEST_KEY", opener=FakeOpener(outcome))
    with pytest.raises(AllProvidersUnavailable) as raised:
        post({})
    (failure,) = raised.value.failures
    assert (failure.provider, failure.kind) == ("modal", kind)


def test_bearer_post_rejects_a_key_that_cannot_be_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MODAL_KEY", "bad\nkey")
    post = BearerPost("https://m.example", api_key_env="TEST_MODAL_KEY", opener=FakeOpener(b"{}"))
    with pytest.raises(AllProvidersUnavailable) as raised:
        post({})
    assert raised.value.failures[0].kind == "config"
    assert "bad" not in str(raised.value)


class FakeTokenizer:
    chat_template = "qwen"

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return {"input_ids": [1, 2, 3]}


def test_modal_backend_sends_the_key_and_renders_thinking_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MODAL_KEY", "k")
    opener = FakeOpener(b'{"output_ids": [5], "meta_info": {"finish_reason": {"type": "length"}}}')
    tokenizer = FakeTokenizer()
    backend = ModalSGLangBackend(
        "https://m.example", tokenizer, model_id="qwen@rev", eos=[0],
        post=BearerPost("https://m.example", api_key_env="TEST_MODAL_KEY", opener=opener),
    )  # fmt: skip
    assert backend.chat([{"role": "user", "content": "q"}]) == (1, 2, 3)
    assert tokenizer.kwargs["enable_thinking"] is False
    assert backend.generate([1, 2], 4)["output_ids"] == [5]
    assert opener.requests[0].get_header("Authorization") == "Bearer k"
    assert json.loads(opener.requests[0].data or b"{}")["input_ids"] == [1, 2]  # type: ignore[arg-type]


def test_ban_needs_a_mask_processor_on_sglang() -> None:
    def backend(processor: str | None) -> ModalSGLangBackend:
        return ModalSGLangBackend(
            "https://m.example", FakeTokenizer(), model_id="m", eos=[0], processor=processor
        )

    assert _can_ban(backend("prebuilt")) is True
    mask_deps = all(importlib.util.find_spec(m) is not None for m in ("torch", "sglang"))
    assert _can_ban(backend(None)) is mask_deps  # False here: neither is a dependency
    assert _can_ban(FakeBackend({})) is True
