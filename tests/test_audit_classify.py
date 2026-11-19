"""Tests for legal classification shift checks (R1, R2)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from legal_rag_verifier.premise import Premise

from chapterverse.audit.classify import (
    CLASSIFICATION_SHIFT,
    CRIMINAL_TERMS,
    TERM_CLASSES,
    classification_failure,
)
from chapterverse.audit.continuation import ChatContinuationGenerator
from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    ChatRequest,
    ChatResult,
    FinishReason,
    JudgeReply,
    Policy,
    SentenceOut,
    TemporalStatus,
    TextStore,
    Window,
)
from chapterverse.store import open_store

# R2.1 Equality Act 2010 s.39(1)
EQ_S39_1_TEXT = (
    "(1) An employer (A) must not discriminate against a person (B)— "
    "(a) in the arrangements A makes for deciding to whom to offer employment; "
    "(b) as to the terms on which A offers B employment; "
    "(c) by not offering B employment."
)

EQ_S39_TRANSCRIPT_SENTENCE = (
    "It is an offence for an employer to discriminate against a person in the "
    "arrangements for deciding to whom to offer employment, as to the terms of "
    "that offer, or by not offering employment."
)

EQ_S39_CORRECT_SENTENCE = (
    "An employer must not discriminate against a person in the arrangements "
    "for deciding to whom to offer employment."
)

# R2.2 Confirmed from artefacts/store: Health and Safety at Work etc. Act 1974 s.33(1)
# Coordinate: uk/ukpga/1974/37/s33/1
HSWA_S33_1_TEXT = (
    "(1) It is an offence for a person— (a) to fail to discharge a duty to which "
    "he is subject by virtue of sections 2 to 7; (b) to contravene section 8 or 9; "
    "(c) to contravene any health and safety regulations or any requirement or prohibition "
    "imposed under section 14 or by or under regulations made under section 15;"
)

HSWA_OFFENCE_SENTENCE = (
    "It is an offence for a person to fail to discharge a duty to which they are "
    "subject under sections 2 to 7."
)

S124 = "uk/ukpga/1996/18/s124"
QUERY = "What is the maximum compensatory award for unfair dismissal?"
REFUSAL = "I cannot state this point reliably from the provisions provided."

GOOD1 = (
    "The compensatory award cannot exceed the lower of £123,543 and 52 multiplied by a "
    "week\u2019s pay [P2]."
)
GOOD2 = "The limit does not apply in some unfair dismissal cases [P3]."
OFFENCE_DRAFT = "It is an offence to exceed the statutory compensatory award limit [P2]."
TEXT1 = GOOD1.replace(" [P2]", "")
TEXT2 = GOOD2.replace(" [P3]", "")


# ---------------------------------------------------------------------- fakes for R2.4


class FakeRotation:
    """Returns scripted blocks in order."""

    def __init__(self, script: Sequence[tuple[str, FinishReason]]) -> None:
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    def complete(self, req: ChatRequest) -> ChatResult:
        self.requests.append(req)
        if not self.script:
            raise AssertionError("the generator asked for more blocks than scripted")
        text, finish = self.script.pop(0)
        return ChatResult(text, finish, "groq", "qwen/qwen3.8-27b", None, 900, 60, 12.5)

    def available(self) -> list[str]:
        return ["groq"]


class FakeJudgeChat:
    """Labels any sentence supported; records every checked sentence."""

    model = "fake-judge"

    def __init__(self) -> None:
        self.sentences: list[str] = []

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        user = messages[1]["content"]
        sentence = user.split("Sentence to check:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
        self.sentences.append(sentence)
        content = json.dumps({"label": "supported", "passage": 1})
        return JudgeReply(content=content, ms=3.0, tokens=7, finish="stop")


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


Run = tuple[list[SentenceOut], FakeRotation, FakeJudgeChat, ChatContinuationGenerator]


@pytest.fixture
def run_chat(premise: Premise, windows: tuple[Window, ...], policy: Policy) -> Callable[..., Run]:
    def run(script: Sequence[tuple[str, FinishReason]], **kwargs: Any) -> Run:
        rotation = FakeRotation(script)
        judge = FakeJudgeChat()
        gen = ChatContinuationGenerator(
            rotation, judge, policy, query=QUERY, premise=premise, windows=windows, **kwargs
        )
        out = list(gen)
        assert not rotation.script, "scripted blocks left over"
        return out, rotation, judge, gen

    return run


# ---------------------------------------------------------------------- R2.1 tests


def test_equality_act_s39_transcript_sentence_fails() -> None:
    fail = classification_failure(EQ_S39_TRANSCRIPT_SENTENCE, [EQ_S39_1_TEXT])
    assert fail is not None
    assert "offence" in fail


def test_equality_act_s39_correct_sentence_passes() -> None:
    assert classification_failure(EQ_S39_CORRECT_SENTENCE, [EQ_S39_1_TEXT]) is None


# ---------------------------------------------------------------------- R2.2 tests


def test_real_offence_provision_passes() -> None:
    assert classification_failure(HSWA_OFFENCE_SENTENCE, [HSWA_S33_1_TEXT]) is None


def test_real_offence_provision_confirmed_via_store() -> None:
    # Confirms that HSWA s.33(1) actually exists and contains offence terms
    try:
        store = open_store("artefacts/store")
    except (FileNotFoundError, OSError):
        pytest.skip("artefacts/store not available in this environment")
    records = store.get("uk/ukpga/1974/37/s33")
    sub1 = next((r for r in records if r.get("coordinate") == "uk/ukpga/1974/37/s33/1"), None)
    assert sub1 is not None
    assert "offence" in sub1["text"].lower()


# ---------------------------------------------------------------------- R2.3 tests


@pytest.mark.parametrize(
    ("sentence", "passage_text"),
    [
        ("The defendant committed an offence.", "A person who commits offences under this Act..."),
        ("He was convicted of two crimes.", "A person is guilty of a crime if..."),
        ("A summary conviction applies.", "A person guilty of an offence under this section..."),
        ("The prosecution must prove guilt.", "Any person prosecuted under this Act..."),
        ("The offender faces a fine.", "The court may impose a sentence of imprisonment."),
        ("He was indicted by the court.", "An indictable offence under section 4."),
    ],
)
def test_inflections_treated_as_one_class(sentence: str, passage_text: str) -> None:
    assert classification_failure(sentence, [passage_text]) is None


@pytest.mark.parametrize(
    ("sentence", "passage_text", "should_fail"),
    [
        ("It is an OFFENCE to enter.", "It is an offence to enter.", False),
        ("It is an Offence to enter.", "No entry permitted under any circumstances.", True),
        ("A CRIME was reported.", "Any crime committed under this part.", False),
        ("A crime was reported.", "Any CRIME committed under this part.", False),
    ],
)
def test_case_insensitive_matching(sentence: str, passage_text: str, should_fail: bool) -> None:
    detail = classification_failure(sentence, [passage_text])
    if should_fail:
        assert detail is not None
    else:
        assert detail is None


def test_negation_fails_when_passage_lacks_class_term() -> None:
    # R1.4: Negated sentences must fail if the passage does not contain the term
    sentence = "It is not an offence to decline the offer."
    assert classification_failure(sentence, [EQ_S39_1_TEXT]) == "offence"


def test_negation_passes_when_passage_contains_class_term() -> None:
    # R1.4: Negated sentences pass when the passage does contain the term
    sentence = "It is not an offence to fail to discharge the duty."
    assert classification_failure(sentence, [HSWA_S33_1_TEXT]) is None


def test_no_citation_fails_when_sentence_contains_term() -> None:
    # R1.3: A sentence citing no passage that contains a term fails
    assert classification_failure("This constitutes an offence.", []) == "offence"


def test_no_citation_passes_when_sentence_contains_no_term() -> None:
    assert classification_failure("This is a simple factual sentence.", []) is None


def test_multi_passage_grounded_by_any_matching_passage() -> None:
    sentence = "It is an offence to act."
    # Grounded because second passage has offence term
    assert classification_failure(sentence, [EQ_S39_1_TEXT, HSWA_S33_1_TEXT]) is None


def test_multi_passage_fails_when_no_passage_has_matching_term() -> None:
    sentence = "It is an offence to act."
    assert classification_failure(sentence, [EQ_S39_1_TEXT, EQ_S39_1_TEXT]) == "offence"


def test_word_boundaries_do_not_match_substrings() -> None:
    # 'discriminate' contains 'crim' but is not 'crime' or 'criminal'
    sentence = "An employer must not discriminate against an employee."
    assert classification_failure(sentence, ["Civil employment rules."]) is None

    # 'inoffensive' contains 'offence' substring but is not 'offence'
    sentence2 = "The action was entirely inoffensive."
    assert classification_failure(sentence2, ["Civil employment rules."]) is None


def test_all_criminal_terms_are_in_class() -> None:
    assert CLASSIFICATION_SHIFT == "CLASSIFICATION_SHIFT"
    required = {
        "convicted",
        "conviction",
        "crime",
        "crimes",
        "criminal",
        "guilty",
        "imprisonment",
        "indictable",
        "indictment",
        "offence",
        "offences",
        "offender",
        "prosecuted",
        "prosecution",
        "summary conviction",
    }
    assert required.issubset(CRIMINAL_TERMS)
    assert TERM_CLASSES["criminal"] == CRIMINAL_TERMS


# ---------------------------------------------------------------------- R2.4 end-to-end tests


def test_end_to_end_offence_sentence_is_rolled_back_and_retried(
    run_chat: Callable[..., Run],
) -> None:
    script = [(f"{OFFENCE_DRAFT} {GOOD2}", "stop"), (f"{GOOD1} {GOOD2}", "stop")]
    out, _, judge, gen = run_chat(script)

    # First sentence was rolled back and retried; retry's correct sentence emitted
    assert len(out) == 2
    assert out[0] == SentenceOut(0, TEXT1, "supported", ("P2",), ("GROUNDED",), 1)
    assert out[1] == SentenceOut(1, TEXT2, "supported", ("P3",), ("GROUNDED",), 0)

    # Judge was only called for valid sentences, never for the offence sentence
    assert judge.sentences == [TEXT1, TEXT2]
    assert gen.stop_reason == "stop"


def test_end_to_end_repeated_offence_is_refused_with_classification_shift(
    run_chat: Callable[..., Run],
) -> None:
    wrong = (OFFENCE_DRAFT, "stop")
    script = [wrong, wrong, wrong, wrong, ("", "stop")]
    out, _, judge, gen = run_chat(script)

    assert len(out) == 1
    assert out[0].verdict == "refused"
    assert out[0].rollbacks == 4
    assert out[0].text == REFUSAL
    assert "CLASSIFICATION_SHIFT" in out[0].reasons
    assert "UNGROUNDED_CITATION" in out[0].reasons
    # Judge was never called
    assert judge.sentences == []
    assert gen.stop_reason == "stop"
