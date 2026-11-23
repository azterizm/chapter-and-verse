"""End-to-end integration tests for Slice 2 (requirements R3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from legal_rag_router import Router

from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    ClarifyEvent,
    Event,
    NoticeEvent,
    ReceiptEvent,
    RefusalEvent,
    SentenceEvent,
    TextStore,
    sha256_hex,
    validate_order,
)
from chapterverse.nlp import analyse
from chapterverse.normalise import find_protected_spans, load_dictionary, normalise
from chapterverse.pii import mask_pii
from chapterverse.pipeline import AskRequest, Outcome, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME

if TYPE_CHECKING:
    from tests.test_pipeline import CountingStore
    from tests.test_pipeline_fakes import GOOD1, GOOD2, S124_QUERY, FakeRotation
else:
    from test_pipeline import CountingStore
    from test_pipeline_fakes import GOOD1, GOOD2, S124_QUERY, FakeRotation

POLICY = build_policy(JUDGE_PRESET_NAME)


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(fixture_text_store: TextStore) -> CountingStore:
    return CountingStore(fixture_text_store)


@pytest.fixture(scope="session")
def symspell_dict() -> Any:
    return load_dictionary(REPO_ROOT / "artefacts" / "symspell" / "dictionary.txt")


def _make_pipeline(
    router: Router, store: TextStore, rotation: Any, dictionary: Any = None, **kwargs: Any
) -> Pipeline:
    if dictionary is None:
        dict_path = REPO_ROOT / "artefacts" / "symspell" / "dictionary.txt"
        if dict_path.is_file():
            dictionary = load_dictionary(dict_path)
    return Pipeline(
        router=router,
        store=store,
        rotation=rotation,
        policy=POLICY,
        dictionary=dictionary,
        **kwargs,
    )


def _run(pipeline: Pipeline, query: str, **kwargs: Any) -> tuple[list[Event], Outcome]:
    events: list[Event] = []
    outcome = pipeline.run(AskRequest(query=query, **kwargs), events.append)
    types = [e.type for e in events]
    assert validate_order(types), types
    return events, outcome


def test_spelling_corrections_and_concept_discovery(
    fixture_router: Router, store: CountingStore
) -> None:
    """1. "unfiar dismisal compensatory award cap": corrections notice, then clarify event

    with discovered candidates (fixture concept index), no provider call.
    """
    rotation = FakeRotation()
    pipeline = _make_pipeline(fixture_router, store, rotation)
    events, outcome = _run(pipeline, "unfiar dismisal compensatory award cap")

    assert outcome.mode == "clarify"
    assert not rotation.requests  # no provider call

    # Event sequence: route -> notice -> clarify -> done
    types = [e.type for e in events]
    assert types == ["route", "notice", "clarify", "done"]

    notice = events[1]
    assert isinstance(notice, NoticeEvent)
    assert notice.code == "corrections_applied"
    assert '"unfiar" read as "unfair"' in notice.message
    assert '"dismisal" read as "dismissal"' in notice.message

    clarify = events[2]
    assert isinstance(clarify, ClarifyEvent)
    assert len(clarify.candidates) > 0


def test_negative_evidence_refuses_no_provider_call(
    fixture_router: Router, store: CountingStore
) -> None:
    """2. Gibberish: negative-evidence refusal, no provider call."""
    rotation = FakeRotation()
    pipeline = _make_pipeline(fixture_router, store, rotation)
    events, outcome = _run(pipeline, "teleportation spacecraft asteroid")

    assert outcome.mode == "refusal"
    assert not rotation.requests  # no provider call

    refusals = [e for e in events if isinstance(e, RefusalEvent)]
    assert len(refusals) == 1
    assert refusals[0].kind == "ROUTE_UNRESOLVED"

    receipts = [e for e in events if isinstance(e, ReceiptEvent)]
    assert len(receipts) == 1
    assert receipts[0].fact.mode == "refusal"


def test_out_of_scope_refusal(fixture_router: Router, store: CountingStore) -> None:
    """3. "What does US federal law say about overtime?": out_of_scope refusal."""
    rotation = FakeRotation()
    pipeline = _make_pipeline(fixture_router, store, rotation)
    events, outcome = _run(pipeline, "What does US federal law say about overtime?")

    assert outcome.mode == "refusal"
    assert not rotation.requests  # no provider call

    refusals = [e for e in events if isinstance(e, RefusalEvent)]
    assert len(refusals) == 1
    assert refusals[0].kind == "out_of_scope"

    receipts = [e for e in events if isinstance(e, ReceiptEvent)]
    assert len(receipts) == 1
    assert receipts[0].fact.mode == "refusal"


def test_pii_masking_provider_isolation_and_unmasking(
    fixture_router: Router, store: CountingStore
) -> None:
    """4. "Can Priya Nkemelu claim under s.124 ERA 1996?":

    - every request the fake rotation and fake judge receive contains no "Priya" and no "Nkemelu";
    - the streamed sentence the client receives shows the name if the fake model wrote placeholder;
    - receipt stores only the masked sentence text and query_sha256 of the raw query, never names.
    """
    raw_query = "Can Priya Nkemelu claim under s.124 ERA 1996?"
    # Model generates sentence with placeholder and cited passage
    fake_sentence = (
        "The compensatory award for [PERSON 1] cannot exceed the lower of £123,543 and 52 "
        "multiplied by a week\u2019s pay [P2]."
    )
    rotation = FakeRotation([(fake_sentence, "stop")])
    pipeline = _make_pipeline(fixture_router, store, rotation)

    events, outcome = _run(pipeline, raw_query)
    assert outcome.mode == "generated"

    # Verify that rotation received requests (generation and judge calls)
    assert len(rotation.requests) > 0
    for req in rotation.requests:
        content = " ".join(m.get("content", "") for m in req.messages)
        assert "Priya" not in content, f"Leaked 'Priya' in provider call: {content}"
        assert "Nkemelu" not in content, f"Leaked 'Nkemelu' in provider call: {content}"
        assert "[PERSON 1]" in content

    # Streamed sentence to the client has the unmasked name
    sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
    assert len(sentence_events) == 1
    client_sentence = sentence_events[0].sentence.text
    assert "Priya Nkemelu" in client_sentence
    assert "[PERSON 1]" not in client_sentence
    assert "The compensatory award for Priya Nkemelu cannot exceed the lower of" in client_sentence

    # Receipt stores only the masked sentence and raw query_sha256
    receipts = [e for e in events if isinstance(e, ReceiptEvent)]
    assert len(receipts) == 1
    fact = receipts[0].fact
    assert fact.query_sha256 == sha256_hex(raw_query.encode("utf-8"))
    assert len(fact.sentences) == 1
    receipt_sentence = fact.sentences[0].text
    assert "[PERSON 1]" in receipt_sentence
    assert "Priya" not in receipt_sentence
    assert "Nkemelu" not in receipt_sentence


def test_name_protection_prevents_false_spelling_corrections(
    fixture_router: Router, symspell_dict: Any
) -> None:
    """R1.3: "Can Priya Nkemelu claim redundancy pay from Zentrix Ltd?" yields no corrections."""
    raw = "Can Priya Nkemelu claim redundancy pay from Zentrix Ltd?"
    citation_spans = find_protected_spans(raw, router=fixture_router)
    analysis = analyse(raw, protected_spans=citation_spans)
    router_index = getattr(fixture_router, "index", None)
    vocab = frozenset(k.lower() for k in symspell_dict.words)
    masked = mask_pii(
        raw,
        analysis,
        router_index=router_index,
        protected_spans=citation_spans,
        vocabulary=vocab,
    )
    name_spans = list(masked.name_spans)
    protect_spans = list(citation_spans) + name_spans

    norm = normalise(raw, dictionary=symspell_dict, router=fixture_router, protect=protect_spans)
    assert len(norm.corrections) == 0
    assert norm.text == raw


def test_no_query_text_or_names_in_logs(
    fixture_router: Router, store: CountingStore, caplog: pytest.LogCaptureFixture
) -> None:
    """R1.8 & R3: Never log raw or masked query text, names or corrections; log counts only."""
    caplog.set_level(logging.DEBUG)
    raw_query = "Can Priya Nkemelu claim under s.124 ERA 1996?"
    fake_sentence = "Under section 124, [PERSON 1] may claim an award limited to £123,543 [P2]."
    rotation = FakeRotation([(fake_sentence, "stop")])
    pipeline = _make_pipeline(fixture_router, store, rotation)

    _events, outcome = _run(pipeline, raw_query)

    summary = outcome.summary()
    assert "query" not in summary
    assert "raw" not in summary
    assert "corrections" not in summary
    assert summary["query_sha256"] == sha256_hex(raw_query.encode("utf-8"))

    all_logs = " ".join(record.getMessage() for record in caplog.records)
    assert "Priya" not in all_logs
    assert "Nkemelu" not in all_logs
    assert raw_query not in all_logs
    assert "[PERSON 1]" not in all_logs


def test_cited_questions_behave_as_before(fixture_router: Router, store: CountingStore) -> None:
    """R3: Cited questions behave exactly as before."""
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")])
    pipeline = _make_pipeline(fixture_router, store, rotation)
    events, outcome = _run(pipeline, S124_QUERY)

    assert outcome.mode == "generated"
    assert [e.type for e in events] == [
        "route",
        "sources",
        "sentence",
        "sentence",
        "receipt",
        "done",
    ]


def test_r6_gibberish_negative_evidence_no_corrections(
    fixture_router: Router, store: CountingStore
) -> None:
    """R6: 'zxqv blorptastic wibbleflange' yields NoEvidence refusal and no corrections notice."""
    rotation = FakeRotation()
    pipeline = _make_pipeline(fixture_router, store, rotation)
    events, outcome = _run(pipeline, "zxqv blorptastic wibbleflange")

    assert outcome.mode == "refusal"
    assert not rotation.requests

    notices = [e for e in events if isinstance(e, NoticeEvent) and e.code == "corrections_applied"]
    assert len(notices) == 0

    refusals = [e for e in events if isinstance(e, RefusalEvent)]
    assert len(refusals) == 1
    assert refusals[0].kind == "ROUTE_UNRESOLVED"
    assert "No provision matched." in refusals[0].message

    receipts = [e for e in events if isinstance(e, ReceiptEvent)]
    assert len(receipts) == 1
    assert receipts[0].fact.mode == "refusal"
