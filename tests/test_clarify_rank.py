"""Tests for clarify candidate ranking and optional priors (S5.3, R5)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from legal_rag_router import Router, RouteStatus

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chapterverse.app import _load_clarify_prior  # noqa: E402
from chapterverse.audit.policy import build_policy  # noqa: E402
from chapterverse.contracts import (  # noqa: E402
    ChatRequest,
    ChatResult,
    ClarifyEvent,
    Event,
    RouteEvent,
    SourcesEvent,
    TextStore,
)
from chapterverse.dense.retriever import DenseHit, DenseRetriever  # noqa: E402
from chapterverse.discovery import (  # noqa: E402
    ClarifyPrior,
    Suggest,
    discover_step,
    load_clarify_prior,
)
from chapterverse.pipeline import AskRequest, Pipeline  # noqa: E402
from chapterverse.providers.presets import JUDGE_PRESET_NAME  # noqa: E402
from eval import clarify_rank as eval_clarify_rank  # noqa: E402
from eval import discovery_gate as eval_discovery_gate  # noqa: E402


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
    assert load_clarify_prior(None, router_snapshot="2026-09-28") is None
    assert load_clarify_prior(tmp_path / "absent.json", router_snapshot="2026-09-28") is None

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    assert load_clarify_prior(bad_json, router_snapshot="2026-09-28") is None

    bad_ver = tmp_path / "ver.json"
    bad_ver.write_text(json.dumps({"format_version": 99}), encoding="utf-8")
    assert load_clarify_prior(bad_ver, router_snapshot="2026-09-28") is None

    valid = tmp_path / "valid.json"
    payload: dict[str, Any] = {
        "format_version": 1,
        "snapshot": "2026-09-28",
        "sections": {"uk/ukpga/1996/18/s124": 7},
        "instruments": {"uk/ukpga/1996/18": 15},
    }
    valid.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_clarify_prior(valid, router_snapshot="2026-09-28")
    assert loaded is not None
    assert loaded.snapshot == "2026-09-28"
    assert loaded.section_count("uk/ukpga/1996/18/s124/1") == 7
    assert loaded.instrument_count("uk/ukpga/1996/18/s124") == 15


def test_load_clarify_prior_snapshot_mismatch_gives_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    empty_snap = tmp_path / "empty_snap.json"
    empty_snap.write_text(
        json.dumps({"format_version": 1, "snapshot": "", "sections": {"uk/ukpga/1996/18/s1": 1}}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="chapterverse.discovery"):
        assert load_clarify_prior(empty_snap, router_snapshot="2026-09-28") is None
    assert len(caplog.records) == 1

    caplog.clear()
    stale_snap = tmp_path / "stale_snap.json"
    stale_snap.write_text(
        json.dumps(
            {"format_version": 1, "snapshot": "2026-09-01", "sections": {"uk/ukpga/1996/18/s1": 1}}
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="chapterverse.discovery"):
        assert load_clarify_prior(stale_snap, router_snapshot="2026-09-28") is None
    assert len(caplog.records) == 1


def test_pipeline_default_does_not_read_filesystem_for_prior(
    fixture_router: Router,
    fixture_text_store: TextStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_file = tmp_path / "prior.json"
    prior_file.write_text(
        json.dumps(
            {
                "format_version": 1,
                "snapshot": fixture_router.index.snapshot,
                "sections": {"uk/ukpga/1996/18/s124": 100},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLARIFY_PRIOR_PATH", str(prior_file))

    def _forbid_read(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Pipeline() must not read clarify prior files")

    monkeypatch.setattr("chapterverse.discovery.load_clarify_prior", _forbid_read)

    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=_EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
    )
    assert pipeline.clarify_prior is None

    # Even with a non-partial router manifest, default Pipeline() still has no prior.
    non_partial_router = MagicMock(spec=Router)
    non_partial_router.index = MagicMock(snapshot="2026-09-28", manifest={"partial": False})
    non_partial_router.concepts = None
    non_partial_router._concepts = None
    pipeline_full = Pipeline(
        router=non_partial_router,
        store=fixture_text_store,
        rotation=_EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
    )
    assert pipeline_full.clarify_prior is None


def test_app_load_clarify_prior_env(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="chapterverse.app"):
        assert _load_clarify_prior({}, router_snapshot="2026-09-28") is None
    assert any("clarify prior off" in r.message and "unset" in r.message for r in caplog.records)

    prior_file = tmp_path / "prior.json"
    prior_file.write_text(
        json.dumps(
            {
                "format_version": 1,
                "snapshot": "2026-09-28",
                "sections": {"uk/ukpga/1996/18/s124": 7},
                "instruments": {"uk/ukpga/1996/18": 15},
            }
        ),
        encoding="utf-8",
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="chapterverse.app"):
        loaded = _load_clarify_prior(
            {"CLARIFY_PRIOR_PATH": str(prior_file)}, router_snapshot="2026-09-28"
        )
    assert loaded is not None
    assert any("clarify prior on" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="chapterverse.app"):
        mismatched = _load_clarify_prior(
            {"CLARIFY_PRIOR_PATH": str(prior_file)}, router_snapshot="2026-09-29"
        )
    assert mismatched is None
    assert any("clarify prior off" in r.message for r in caplog.records)


def test_eval_prior_flags_and_pipeline_wiring(
    fixture_router: Router, fixture_text_store: TextStore, tmp_path: Path
) -> None:
    custom = tmp_path / "custom.json"
    custom.write_text(
        json.dumps(
            {
                "format_version": 1,
                "snapshot": fixture_router.index.snapshot,
                "sections": {"uk/ukpga/1996/18/s124": 5},
            }
        ),
        encoding="utf-8",
    )

    for parser_fn in (eval_discovery_gate._parse_args, eval_clarify_rank._parse_args):
        default_args = parser_fn([])
        assert default_args.prior == Path("artefacts/clarify/clarify_prior.json")
        assert default_args.no_prior is False

        no_prior_args = parser_fn(["--no-prior"])
        assert no_prior_args.no_prior is True

        custom_args = parser_fn(["--prior", str(custom)])
        assert custom_args.prior == custom
        assert custom_args.no_prior is False

    loaded = eval_discovery_gate.load_eval_clarify_prior(
        custom, no_prior=False, router_snapshot=fixture_router.index.snapshot
    )
    assert loaded is not None
    assert (
        eval_discovery_gate.load_eval_clarify_prior(
            custom, no_prior=True, router_snapshot=fixture_router.index.snapshot
        )
        is None
    )
    assert (
        eval_discovery_gate.load_eval_clarify_prior(
            custom, no_prior=False, router_snapshot="different-snapshot"
        )
        is None
    )

    _, _, p_nodense_on, _, _ = eval_discovery_gate._build_pipelines(
        fixture_router, fixture_text_store, None, tmp_path, prior=loaded
    )
    assert p_nodense_on.clarify_prior is loaded

    _, _, p_nodense_off, _, _ = eval_discovery_gate._build_pipelines(
        fixture_router, fixture_text_store, None, tmp_path, prior=None
    )
    assert p_nodense_off.clarify_prior is None

    assert eval_discovery_gate.clarify_prior_report(loaded, prior_path=custom, no_prior=False) == {
        "on": True,
        "path": str(custom),
        "snapshot": fixture_router.index.snapshot,
    }
    assert eval_discovery_gate.clarify_prior_report(None, prior_path=custom, no_prior=True) == {
        "on": False,
        "path": None,
        "snapshot": None,
    }


def test_build_clarify_prior_writes_manifest(tmp_path: Path) -> None:
    import hashlib  # noqa: PLC0415

    from scripts import build_clarify_prior  # noqa: PLC0415

    assert Path("artefacts/clarify/clarify_prior.json") == build_clarify_prior.DEFAULT_OUT

    repo = tmp_path / "router"
    (repo / "data" / "index").mkdir(parents=True)
    (repo / "data" / "index" / "index-manifest.json").write_text(
        json.dumps({"snapshot": "2026-09-28"}), encoding="utf-8"
    )
    out = tmp_path / "clarify" / "clarify_prior.json"
    assert build_clarify_prior.main(["--router-repo", str(repo), "--out", str(out)]) == 0

    raw = out.read_bytes()
    prior = json.loads(raw)
    assert prior["format_version"] == 1
    assert prior["snapshot"] == "2026-09-28"

    manifest = json.loads((out.parent / "clarify-manifest.json").read_bytes())
    assert manifest == {
        "snapshot": "2026-09-28",
        "files": {
            "clarify_prior.json": {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        },
    }
