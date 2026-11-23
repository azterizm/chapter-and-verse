"""Tests for private documents and bare section handling (specs/private-docs/requirements.md)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from legal_rag_router import Router
from symspellpy import SymSpell  # type: ignore[import-untyped]

from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    ClarifyEvent,
    Event,
    ReceiptEvent,
    RefusalEvent,
    TextStore,
    validate_order,
)
from chapterverse.discovery import (
    is_named_private_document,
    remove_bare_section,
)
from chapterverse.pipeline import AskRequest, Outcome, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.templates import (
    CLARIFY_RELATED_PROVISIONS,
    CLARIFY_WHICH_ACT,
    PRIVATE_DOCUMENTS_MESSAGE,
)

if TYPE_CHECKING:
    from tests.test_pipeline import CountingStore
    from tests.test_pipeline_fakes import GOOD1, FakeRotation
else:
    from test_pipeline import CountingStore
    from test_pipeline_fakes import GOOD1, FakeRotation

POLICY = build_policy(JUDGE_PRESET_NAME)

MAYFAIR_QUERY = (
    "Under Section 4 of the Mayfair Lease Agreement, who bears the cost of routine maintenance "
    "and filter replacements for internal HVAC units?"
)


def _make_pipeline(router: Router, store: TextStore, rotation: Any, **kwargs: Any) -> Pipeline:
    dict_obj = kwargs.pop("dictionary", SymSpell())
    return Pipeline(
        router=router,
        store=store,
        rotation=rotation,
        policy=POLICY,
        dictionary=dict_obj,
        **kwargs,
    )


def _run(pipeline: Pipeline, query: str, **kwargs: Any) -> tuple[list[Event], Outcome]:
    events: list[Event] = []
    outcome = pipeline.run(AskRequest(query=query, **kwargs), events.append)
    types = [e.type for e in events]
    assert validate_order(types), types
    return events, outcome


def test_mayfair_lease_agreement_gives_out_of_scope_refusal_no_provider_call(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    """R1, R3: Mayfair Lease Agreement gives out_of_scope refusal with no provider call."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    events, outcome = _run(pipeline, MAYFAIR_QUERY)

    assert outcome.mode == "refusal"
    assert not rotation.requests  # no provider call
    assert not store.gets

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "refusal", "receipt", "done"]

    refusal = events_core[1]
    assert isinstance(refusal, RefusalEvent)
    assert refusal.kind == "out_of_scope"
    assert refusal.message == PRIVATE_DOCUMENTS_MESSAGE
    assert refusal.suggestions == ()
    assert refusal.link is None

    receipt = events_core[2]
    assert isinstance(receipt, ReceiptEvent)
    assert receipt.fact.mode == "refusal"


def test_my_lease_gives_out_of_scope_refusal_no_provider_call(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    """R1, R3: 'What does section 4 of my lease say about repairs?' gives out_of_scope refusal."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    query = "What does section 4 of my lease say about repairs?"
    events, outcome = _run(pipeline, query)

    assert outcome.mode == "refusal"
    assert not rotation.requests
    assert not store.gets

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "refusal", "receipt", "done"]

    refusal = events_core[1]
    assert isinstance(refusal, RefusalEvent)
    assert refusal.kind == "out_of_scope"
    assert refusal.message == PRIVATE_DOCUMENTS_MESSAGE
    assert refusal.suggestions == ()


def test_bare_section_compensatory_award_gives_discovery_candidates(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    """R2, R3: Bare section 4 query on compensatory award gives discovery candidates."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    query = "What does section 4 say about the limit of the compensatory award?"
    events, outcome = _run(pipeline, query)

    assert outcome.mode == "clarify"
    assert not rotation.requests

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "clarify", "done"]

    clarify = events_core[1]
    assert isinstance(clarify, ClarifyEvent)
    assert clarify.question == CLARIFY_RELATED_PROVISIONS
    assert len(clarify.candidates) > 0

    # Candidates must be related to compensatory award (s.124 ERA 1996),
    # not unrelated s.4 provisions
    coords = [c.coordinate for c in clarify.candidates]
    assert "uk/ukpga/1996/18/s124" in coords
    # Check that unrelated section 4 candidates from routing pool are NOT shown
    for c in clarify.candidates:
        assert not c.coordinate.endswith("/s4")


def test_bare_section_without_discovery_candidates_asks_which_act(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    """R2: Bare section 4 query with no discovery match asks 'Which Act or SI do you mean?'."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    query = "What does section 4 say?"
    events, outcome = _run(pipeline, query)

    assert outcome.mode == "clarify"
    assert not rotation.requests

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "clarify", "done"]

    clarify = events_core[1]
    assert isinstance(clarify, ClarifyEvent)
    assert clarify.question == CLARIFY_WHICH_ACT
    assert clarify.candidates == ()


def test_bare_section_with_only_section_number_asks_which_act(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    """R2: 'What does section 124 say?' asks 'Which Act or SI do you mean?' with no candidates."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    query = "What does section 124 say?"
    events, outcome = _run(pipeline, query)

    assert outcome.mode == "clarify"
    assert not rotation.requests

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "clarify", "done"]

    clarify = events_core[1]
    assert isinstance(clarify, ClarifyEvent)
    assert clarify.question == CLARIFY_WHICH_ACT
    assert clarify.candidates == ()


def test_era_section_124_still_binds(fixture_router: Router, fixture_text_store: TextStore) -> None:
    """R3: 'section 124 of the Employment Rights Act 1996' still binds."""
    rotation = FakeRotation([(GOOD1, "stop")])
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    query = "section 124 of the Employment Rights Act 1996"
    events, outcome = _run(pipeline, query)

    assert outcome.mode == "generated"
    types = [e.type for e in events]
    assert types == ["route", "sources", "sentence", "receipt", "done"]
    assert store.gets == ["uk/ukpga/1996/18/s124"]


def test_rules_in_title_when_router_knows_title_is_not_private_document(
    fixture_router: Router,
) -> None:
    """R3: 'Rules' in titles is not treated as a private document when router knows title."""
    # When router index knows the title
    mock_router = MagicMock(spec=Router)
    mock_index = MagicMock()
    mock_index.tables = {
        "titles": {"civil procedure rules|1998": "uk/uksi/1998/3132"},
        "coverage_titles": {},
    }
    mock_router.index = mock_index

    # Known title is NOT treated as a private document
    query_with_rule = "Under rule 3.1 of Civil Procedure Rules 1998"
    assert not is_named_private_document(query_with_rule, mock_router)
    assert not is_named_private_document("Civil Procedure Rules 1998", mock_router)

    # Unknown title with 'Rules' (e.g. Staff Rules) IS treated as a private document
    assert is_named_private_document("According to rule 3 of Staff Rules", mock_router)
    assert is_named_private_document("Under the Company Rules", mock_router)


def test_private_doc_detection_phrases_and_types(fixture_router: Router) -> None:
    """R1: Test various document types and phrases."""
    # Phrases
    assert is_named_private_document("According to my lease", fixture_router)
    assert is_named_private_document("As stated in my contract", fixture_router)
    assert is_named_private_document("What is our policy on leave?", fixture_router)
    assert is_named_private_document("Under the agreement, who pays?", fixture_router)
    assert is_named_private_document("In the attached, what does it say?", fixture_router)
    assert is_named_private_document("In the uploaded pdf, what does clause 2 say?", fixture_router)
    assert is_named_private_document("What does this document mean?", fixture_router)

    # Named types with capitalized name
    assert is_named_private_document("Under Mayfair Lease Agreement", fixture_router)
    assert is_named_private_document("According to the Staff Handbook", fixture_router)
    assert is_named_private_document("XYZ Partnership Deed", fixture_router)
    assert is_named_private_document("Acme Corp Code of Conduct", fixture_router)
    assert is_named_private_document("Bank Terms and Conditions", fixture_router)
    assert is_named_private_document("Global Pension Plan", fixture_router)
    assert is_named_private_document("Smith Will", fixture_router)
    assert is_named_private_document("Jones Trust Deed", fixture_router)
    assert is_named_private_document("Company Articles of Association", fixture_router)
    assert is_named_private_document("Board Memorandum", fixture_router)
    assert is_named_private_document("Corporate Bylaws", fixture_router)

    # Normal queries are NOT private documents
    assert not is_named_private_document("unfair dismissal compensatory award cap", fixture_router)
    assert not is_named_private_document("What does section 124 say?", fixture_router)


def test_remove_bare_section_helper() -> None:
    """R2: Helper removes bare sections cleanly."""
    assert (
        remove_bare_section("What does section 4 say about the limit of the compensatory award?")
        == "What does say about the limit of the compensatory award?"
    )
    assert remove_bare_section("Under Section 4, what is the limit?") == "what is the limit?"
    assert remove_bare_section("What does section 124 say?") == "What does say?"
    assert (
        remove_bare_section("What does s.124 say about unfair dismissal?")
        == "What does say about unfair dismissal?"
    )


def test_r5_private_doc_refusals_and_non_refusals(fixture_router: Router) -> None:
    """R4, R5: Questions refused as private docs vs bare mentions not refused."""
    # Refused:
    # 1. The Mayfair question
    assert is_named_private_document(MAYFAIR_QUERY, fixture_router)
    # 2. "What does our policy say about remote working?"
    assert is_named_private_document(
        "What does our policy say about remote working?", fixture_router
    )
    # 3. "Summarise the uploaded document."
    assert is_named_private_document("Summarise the uploaded document.", fixture_router)
    assert is_named_private_document("Summarize the uploaded document.", fixture_router)
    # 4. "Under clause 4 of my lease, who pays for repairs?"
    assert is_named_private_document(
        "Under clause 4 of my lease, who pays for repairs?", fixture_router
    )

    # Named private documents with capitalized names (R4 examples)
    assert is_named_private_document(
        "Under the AcmeCloud contract, what are the SLA terms?", fixture_router
    )
    assert is_named_private_document(
        "What does the ZenithHost SaaS Agreement require?", fixture_router
    )

    # Not refused:
    # 1. "Can my employer change my contract without my consent?"
    assert not is_named_private_document(
        "Can my employer change my contract without my consent?", fixture_router
    )
    # 2. "What notice must my landlord give to end my tenancy agreement?"
    assert not is_named_private_document(
        "What notice must my landlord give to end my tenancy agreement?", fixture_router
    )
    # 3. "Is a settlement agreement binding if I did not get legal advice?"
    assert not is_named_private_document(
        "Is a settlement agreement binding if I did not get legal advice?", fixture_router
    )
    # 4. "What is the minimum notice period under my employment contract?"
    assert not is_named_private_document(
        "What is the minimum notice period under my employment contract?", fixture_router
    )
    # 5. "Does the Highway Code apply to cyclists?"
    assert not is_named_private_document("Does the Highway Code apply to cyclists?", fixture_router)

    # 6. "What does the Civil Procedure Rules 1998 say about disclosure?"
    mock_router = MagicMock(spec=Router)
    mock_index = MagicMock()
    mock_index.tables = {
        "titles": {"civil procedure rules|1998": "uk/uksi/1998/3132"},
        "coverage_titles": {},
    }
    mock_router.index = mock_index
    assert not is_named_private_document(
        "What does the Civil Procedure Rules 1998 say about disclosure?", mock_router
    )


@pytest.mark.parametrize(
    "query",
    [
        "What does our policy say about remote working?",
        "Summarise the uploaded document.",
        "Under clause 4 of my lease, who pays for repairs?",
    ],
)
def test_r5_refused_queries_give_out_of_scope_refusal_no_provider_call(
    fixture_router: Router, fixture_text_store: TextStore, query: str
) -> None:
    """R5: Refused queries give out_of_scope refusal with no provider call."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    events, outcome = _run(pipeline, query)

    assert outcome.mode == "refusal"
    assert not rotation.requests  # no provider call
    assert not store.gets

    events_core = [e for e in events if e.type != "notice"]
    types = [e.type for e in events_core]
    assert types == ["route", "refusal", "receipt", "done"]

    refusal = events_core[1]
    assert isinstance(refusal, RefusalEvent)
    assert refusal.kind == "out_of_scope"
    assert refusal.message == PRIVATE_DOCUMENTS_MESSAGE
    assert refusal.suggestions == ()


@pytest.mark.parametrize(
    "query",
    [
        "Can my employer change my contract without my consent?",
        "What notice must my landlord give to end my tenancy agreement?",
        "Is a settlement agreement binding if I did not get legal advice?",
        "What is the minimum notice period under my employment contract?",
        "Does the Highway Code apply to cyclists?",
    ],
)
def test_r5_not_refused_queries_do_not_give_private_doc_refusal(
    fixture_router: Router, fixture_text_store: TextStore, query: str
) -> None:
    """R5: Questions with bare mentions are NOT refused as private documents."""
    rotation = FakeRotation()
    store = CountingStore(fixture_text_store)
    pipeline = _make_pipeline(fixture_router, store, rotation)

    events, outcome = _run(pipeline, query)
    assert outcome.mode != "closed"

    for e in events:
        if isinstance(e, RefusalEvent):
            assert e.message != PRIVATE_DOCUMENTS_MESSAGE
