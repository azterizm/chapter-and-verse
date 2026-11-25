"""Tests for clarify candidate ranking and optional priors (S5.3, R5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from legal_rag_router import Router, RouteStatus

from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    ChatRequest,
    ChatResult,
    ClarifyEvent,
    Event,
    RouteEvent,
    SourcesEvent,
    TextStore,
)
from chapterverse.dense.retriever import DenseHit, DenseRetriever
from chapterverse.discovery import (
    ClarifyPrior,
    Suggest,
    discover_step,
    load_clarify_prior,
)
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME


class _EmptyRotation:
    def complete(self, req: ChatRequest) -> ChatResult:
        raise AssertionError("citation-less clarify must not call providers")

    def available(self) -> list[str]:
        return []


def test_absent_prior_gives_todays_order_fixture(fixture_router: Router) -> None:
    for query in ("unfair dismissal", "compensatory award limit", "discrimination compensation"):
        default_out = discover_step(fixture_router, query, limit=10)
        none_out = discover_step(fixture_router, query, limit=10, prior=None)
        assert default_out == none_out

    # Synthetic dense hits: prior=None matches default order exactly
    hits = [
        DenseHit(
            doc_id=1,
            coordinate="uk/ukpga/1996/18/s124",
            heading="Limit of compensatory award etc.",
            rank=1,
            score=0.85,
        ),
        DenseHit(
            doc_id=2,
            coordinate="uk/ukpga/1996/18/s98",
            heading="General.",
            rank=2,
            score=0.80,
        ),
    ]
    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = hits

    out_default = discover_step(fixture_router, "unfair dismissal", dense=mock_dense, limit=10)
    out_none = discover_step(
        fixture_router, "unfair dismissal", dense=mock_dense, limit=10, prior=None
    )
    assert out_default == out_none


def test_prior_reorders_synthetic_bm25_tie() -> None:
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/s1", heading="First", score=10.0)
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Second", score=10.0)
    mock_router = MagicMock(spec=Router)
    mock_router.index.snapshot = "2026-09-28"
    mock_router.discover.return_value = MagicMock(
        candidates=(cand_a, cand_b),
        confident=True,
        reason=None,
        index_snapshot="2026-09-28",
    )

    out_absent = discover_step(mock_router, "synthetic tie", limit=2, prior=None)
    assert isinstance(out_absent, Suggest)
    assert [c.coordinate for c in out_absent.candidates] == [
        "uk/ukpga/1996/18/s1",
        "uk/ukpga/1996/18/s2",
    ]

    prior = ClarifyPrior(sections={"uk/ukpga/1996/18/s2": 12})
    out_prior = discover_step(mock_router, "synthetic tie", limit=2, prior=prior)
    assert isinstance(out_prior, Suggest)
    assert [c.coordinate for c in out_prior.candidates] == [
        "uk/ukpga/1996/18/s2",
        "uk/ukpga/1996/18/s1",
    ]


def test_prior_reorders_synthetic_rrf_tie_and_schedule_demotion() -> None:
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/sch1/para1", heading="Sched", score=9.0)
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Section", score=9.0)
    mock_router = MagicMock(spec=Router)
    mock_router.index.snapshot = "2026-09-28"
    mock_router.discover.return_value = MagicMock(
        candidates=(cand_a, cand_b),
        confident=True,
        reason=None,
        index_snapshot="2026-09-28",
    )

    # Identical dense rank tie on cand_a vs cand_b
    hit_b = DenseHit(
        doc_id=1, coordinate="uk/ukpga/1996/18/s2", heading="Section", rank=1, score=0.8
    )
    hit_a = DenseHit(
        doc_id=2, coordinate="uk/ukpga/1996/18/sch1/para1", heading="Sched", rank=1, score=0.8
    )
    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [hit_a, hit_b]

    # Without prior: RRF ties -> BM25 rank 1 (cand_a) wins
    out_absent = discover_step(mock_router, "tie query", dense=mock_dense, limit=2, prior=None)
    assert isinstance(out_absent, Suggest)
    assert [c.coordinate for c in out_absent.candidates] == [
        "uk/ukpga/1996/18/sch1/para1",
        "uk/ukpga/1996/18/s2",
    ]

    # With prior: schedule penalty + section prior promotes main section cand_b
    prior = ClarifyPrior(sections={"uk/ukpga/1996/18/s2": 5})
    out_prior = discover_step(mock_router, "tie query", dense=mock_dense, limit=2, prior=prior)
    assert isinstance(out_prior, Suggest)
    assert [c.coordinate for c in out_prior.candidates] == [
        "uk/ukpga/1996/18/s2",
        "uk/ukpga/1996/18/sch1/para1",
    ]
    assert len(out_prior.candidates) == 2


def test_nothing_binds_with_prior(fixture_router: Router, fixture_text_store: TextStore) -> None:
    prior = ClarifyPrior(
        sections={"uk/ukpga/1996/18/s124": 500, "uk/ukpga/1996/18/s98": 300},
        instruments={"uk/ukpga/1996/18": 100},
    )
    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=_EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
        clarify_prior=prior,
    )

    events: list[Event] = []
    outcome = pipeline.run(AskRequest(query="unfair dismissal compensatory award"), events.append)
    assert outcome.mode == "clarify"
    route_ev = next(e for e in events if isinstance(e, RouteEvent))
    assert route_ev.status == RouteStatus.UNRESOLVED.value
    assert not any(isinstance(e, SourcesEvent) for e in events)
    clarify = next(e for e in events if isinstance(e, ClarifyEvent))
    assert len(clarify.candidates) > 0
    assert any(c.coordinate == "uk/ukpga/1996/18/s124" for c in clarify.candidates)


def test_cited_query_still_binds_with_prior(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    prior = ClarifyPrior(
        sections={"uk/ukpga/1996/18/s124": 500},
        instruments={"uk/ukpga/1996/18": 100},
    )
    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=_EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
        clarify_prior=prior,
    )
    events: list[Event] = []
    outcome = pipeline.run(
        AskRequest(query="What does section 124 of the Employment Rights Act 1996 say?"),
        events.append,
    )
    route_ev = next(e for e in events if isinstance(e, RouteEvent))
    assert route_ev.status == RouteStatus.BOUNDED.value
    assert route_ev.coordinates == ("uk/ukpga/1996/18/s124",)
    assert outcome.mode == "extractive"


def test_load_clarify_prior_optional_and_safe(tmp_path: Path) -> None:
    assert load_clarify_prior(None) is None
    assert load_clarify_prior(tmp_path / "absent.json") is None

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    assert load_clarify_prior(bad_json) is None

    bad_ver = tmp_path / "ver.json"
    bad_ver.write_text(json.dumps({"format_version": 99}), encoding="utf-8")
    assert load_clarify_prior(bad_ver) is None

    valid = tmp_path / "valid.json"
    payload: dict[str, Any] = {
        "format_version": 1,
        "snapshot": "2026-09-28",
        "sections": {"uk/ukpga/1996/18/s124": 7},
        "instruments": {"uk/ukpga/1996/18": 15},
    }
    valid.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_clarify_prior(valid)
    assert loaded is not None
    assert loaded.section_count("uk/ukpga/1996/18/s124/1") == 7
    assert loaded.instrument_count("uk/ukpga/1996/18/s124") == 15
