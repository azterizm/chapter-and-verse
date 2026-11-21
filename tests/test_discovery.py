"""Tests for discovery and negative evidence (specs/s2-discovery/requirements.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from legal_rag_router import Coordinate, Router, RouteStatus

from chapterverse.contracts import (
    ClarifyEvent,
    LabelledCoordinate,
    RefusalEvent,
)
from chapterverse.discovery import (
    NoEvidence,
    Suggest,
    check_out_of_scope,
    discover_step,
    format_candidate_label,
)


def test_out_of_scope_cues(fixture_router: Router) -> None:
    snapshot = fixture_router.index.snapshot

    # Case law cues
    res = check_out_of_scope("Supreme court precedent on unfair dismissal", snapshot)
    assert res is not None
    assert isinstance(res, RefusalEvent)
    assert res.kind == "out_of_scope"
    assert "Case law is not covered." in res.message
    assert res.snapshot == snapshot

    res2 = discover_step(fixture_router, "leading judgment on employee status")
    assert isinstance(res2, RefusalEvent)
    assert res2.kind == "out_of_scope"
    assert "Case law is not covered." in res2.message

    res3 = discover_step(fixture_router, "case-law regarding redundancy consultation")
    assert isinstance(res3, RefusalEvent)
    assert res3.kind == "out_of_scope"

    # Non-UK law cues
    res4 = discover_step(fixture_router, "California overtime regulations")
    assert isinstance(res4, RefusalEvent)
    assert res4.kind == "out_of_scope"
    assert "Only UK legislation is covered." in res4.message

    res5 = discover_step(fixture_router, "US employment discrimination rules")
    assert isinstance(res5, RefusalEvent)
    assert res5.kind == "out_of_scope"
    assert "Only UK legislation is covered." in res5.message

    res6 = discover_step(fixture_router, "labor law in France")
    assert isinstance(res6, RefusalEvent)
    assert res6.kind == "out_of_scope"
    assert "Only UK legislation is covered." in res6.message

    res6b = discover_step(fixture_router, "American worker protection standards")
    assert isinstance(res6b, RefusalEvent)
    assert res6b.kind == "out_of_scope"

    # Personal advice cues
    res7 = discover_step(fixture_router, "should I sign this settlement agreement?")
    assert isinstance(res7, RefusalEvent)
    assert res7.kind == "out_of_scope"
    assert "Advice on a personal situation is not given." in res7.message

    res8 = discover_step(fixture_router, "my lawyer advised me to sue for unfair dismissal")
    assert isinstance(res8, RefusalEvent)
    assert res8.kind == "out_of_scope"
    assert "Advice on a personal situation is not given." in res8.message

    # Regular UK queries should NOT be marked out of scope
    assert check_out_of_scope("unfair dismissal compensatory award cap", snapshot) is None
    assert check_out_of_scope("tell us about written statement of particulars", snapshot) is None


def test_suggestion_path_confident(fixture_router: Router) -> None:
    # "unfair dismissal" in fixture index has confident matches
    outcome = discover_step(fixture_router, "unfair dismissal", limit=10)
    assert isinstance(outcome, Suggest)
    assert outcome.confident is True
    assert outcome.question == "Did you mean one of these provisions?"
    assert 1 <= len(outcome.candidates) <= 10

    # Check candidates format and ClarifyEvent
    event = outcome.event
    assert isinstance(event, ClarifyEvent)
    assert event.type == "clarify"
    assert event.question == outcome.question
    assert event.candidates == outcome.candidates

    # Each candidate must have a readable label and valid coordinate
    for c in outcome.candidates:
        assert isinstance(c, LabelledCoordinate)
        assert c.coordinate.startswith("uk/")
        assert "Employment Rights Act 1996" in c.label
        # Clicking candidate sends coordinate which routes ROUTE_BOUNDED
        route_res = fixture_router.route(c.coordinate)
        assert route_res.status == RouteStatus.BOUNDED
        assert route_res.coordinates == (Coordinate.parse(c.coordinate),)


def test_suggestion_path_low_confidence(fixture_router: Router) -> None:
    # Query matching few terms with low share
    outcome = discover_step(fixture_router, "discrimination compensation minimum", limit=5)
    if isinstance(outcome, Suggest) and not outcome.confident:
        assert (
            outcome.question
            == "These provisions may be related. Choose one, or name the Act and section."
        )
        assert outcome.event.question == outcome.question


def test_suggestion_limit_and_ordering(fixture_router: Router) -> None:
    outcome_3 = discover_step(fixture_router, "unfair dismissal", limit=3)
    assert isinstance(outcome_3, Suggest)
    assert len(outcome_3.candidates) == 3

    outcome_10 = discover_step(fixture_router, "unfair dismissal", limit=10)
    assert isinstance(outcome_10, Suggest)
    assert len(outcome_10.candidates) <= 10

    # Best-first ordering preserved
    for i in range(3):
        assert outcome_3.candidates[i] == outcome_10.candidates[i]


def test_no_evidence_path(fixture_router: Router) -> None:
    # No match query
    query = "teleportation spacecraft asteroid"
    outcome = discover_step(fixture_router, query)
    assert isinstance(outcome, NoEvidence)
    receipt = outcome.receipt
    assert isinstance(receipt, RefusalEvent)
    assert receipt.type == "refusal"
    assert receipt.kind == "ROUTE_UNRESOLVED"
    assert receipt.snapshot == fixture_router.index.snapshot
    assert receipt.suggestions == ()
    assert receipt.link is None

    expected_msg = (
        f"Searched 23 provisions in the statute book as of {fixture_router.index.snapshot} for: "
        f"teleport spacecraft asteroid. No provision matched."
    )
    assert receipt.message == expected_msg
    assert outcome.event == receipt

    # Empty / stop-words query
    outcome_empty = discover_step(fixture_router, "the and for")
    assert isinstance(outcome_empty, NoEvidence)
    assert outcome_empty.receipt.kind == "ROUTE_UNRESOLVED"
    expected_empty_msg = (
        f"Searched 23 provisions in the statute book as of {fixture_router.index.snapshot} for: . "
        f"No provision matched."
    )
    assert outcome_empty.receipt.message == expected_empty_msg


def test_format_candidate_label(fixture_router: Router) -> None:
    # Provision with heading
    lbl1 = format_candidate_label("uk/ukpga/1996/18/s98", "General.", fixture_router)
    assert lbl1 == "s.98 Employment Rights Act 1996: General."

    # Heading with typography requiring plain_text normalization
    lbl2 = format_candidate_label(
        "uk/ukpga/1996/18/s121",
        "Basic award of two weeks\u2019 pay in certain cases.",
        fixture_router,
    )
    assert (
        lbl2 == "s.121 Employment Rights Act 1996: Basic award of two weeks' pay in certain cases."
    )

    # Provision without heading
    lbl3 = format_candidate_label("uk/ukpga/1996/18/s98", None, fixture_router)
    assert lbl3 == "s.98 Employment Rights Act 1996"

    # Synthetic check with no router
    lbl4 = format_candidate_label("uk/ukpga/1996/18/s98", "Fairness", None)
    assert lbl4 == "s.98 1996 c. 18: Fairness"


@pytest.mark.data
def test_discovery_real_data(real_data_root: Path) -> None:
    router = Router.from_path(real_data_root / "index", concepts=real_data_root / "concepts")
    battery_path = Path.home() / "Code/legal-rag-router/batteries/concept/uk.jsonl"
    if not battery_path.is_file():
        pytest.skip(f"concept battery not found at {battery_path}")

    rows = [
        json.loads(line)
        for line in battery_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    test_rows = [r for r in rows if r.get("split") == "test"]
    assert len(test_rows) == 153

    for row in test_rows[:10]:
        outcome = discover_step(router, row["query"], limit=10)
        assert isinstance(outcome, (Suggest, NoEvidence, RefusalEvent))
