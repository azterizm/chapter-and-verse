"""Tests for Layer 3 verified point-in-time statutory limits (``chapterverse.pit_limits``)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi.testclient import TestClient
from legal_rag_router import Router
from legal_rag_router.coordinate import Coordinate
from legal_rag_router.result import NextAction, RouteResult, RouteStatus

from chapterverse.app import _load_pit_limits, build_runtime, create_app
from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    ChatRequest,
    ChatResult,
    DoneEvent,
    Event,
    ReceiptEvent,
    RefusalEvent,
    SentenceEvent,
    SourcesEvent,
    TextStore,
    canonical_json,
    validate_order,
)
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.pit_limits import PitLimits, load_pit_limits, parse_exact_date
from chapterverse.providers.presets import JUDGE_PRESET_NAME

if TYPE_CHECKING:
    from tests.test_pipeline_fakes import parse_sse
else:
    from test_pipeline_fakes import parse_sse

REPO = Path(__file__).resolve().parents[1]
REAL_PIT_LIMITS = REPO / "artefacts" / "pit" / "limits.json"
SNAPSHOT = "2026-09-28"
PIT_ERA_124_1 = (
    "As at 1 January 2012, what was the maximum compensatory award for unfair dismissal "
    "under section 124 of the Employment Rights Act 1996?"
)


class _NoCallRotation:
    """Rotation that fails the test if any LLM call is attempted."""

    def available(self, purpose: str | None = None) -> list[str]:
        del purpose
        return ["groq"]

    def complete(self, req: ChatRequest) -> ChatResult:
        raise AssertionError(f"unexpected provider call: {req.purpose}")


class _FakeRouteRouter:
    """Minimal router double returning a predetermined ``RouteResult``."""

    def __init__(self, result: RouteResult, snapshot: str = SNAPSHOT) -> None:
        self._result = result
        self.index = type("_Idx", (), {"snapshot": snapshot})()
        self.concepts = None

    def route(self, query: str, context: Any = None) -> RouteResult:
        del query, context
        return self._result


class _FakeInstrumentStore:
    """Minimal ``TextStore`` double for uncovered/non-fixture coordinates."""

    def __init__(
        self,
        instruments: dict[str, dict[str, Any]],
        *,
        corpus_id: str = "fake-corpus",
        snapshot: str = SNAPSHOT,
    ) -> None:
        self._instruments = instruments
        self.corpus_id = corpus_id
        self.snapshot = snapshot

    def get(self, coordinate: str) -> list[dict[str, Any]]:
        inst = self.instrument(coordinate)
        if inst is None:
            raise KeyError(coordinate)
        return [
            inst,
            {
                "record_type": "provision",
                "coordinate": coordinate,
                "parent": inst["coordinate"],
                "text": "Dummy provision text.",
                "source_url": f"https://www.legislation.gov.uk/{coordinate.removeprefix('uk/')}",
            },
        ]

    def instrument(self, coordinate: str) -> dict[str, Any] | None:
        for inst_coord, record in self._instruments.items():
            if coordinate == inst_coord or coordinate.startswith(inst_coord + "/"):
                return record
        return None

    def has_text(self, instrument_coordinate: str) -> bool:
        return instrument_coordinate in self._instruments


def _write_pit_artefact(
    directory: Path,
    rows: Any,
    *,
    snapshot: str = SNAPSHOT,
    override_sha: str | None = None,
    override_size: int | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    limits_path = directory / "limits.json"
    limits_bytes = json.dumps(rows).encode("utf-8")
    limits_path.write_bytes(limits_bytes)
    manifest = {
        "snapshot": snapshot,
        "files": {
            "limits.json": {
                "sha256": override_sha or hashlib.sha256(limits_bytes).hexdigest(),
                "size": len(limits_bytes) if override_size is None else override_size,
            }
        },
    }
    (directory / "pit-manifest.json").write_bytes(canonical_json(manifest))
    return limits_path


def _sample_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "coordinate": "uk/ukpga/1996/18/s124",
        "amount": "£68,400",
        "valid_from": "2011-02-01",
        "valid_to": "2012-02-01",
        "si": "uk/uksi/2010/2926",
        "title": "The Employment Rights (Increase of Limits) Order 2010",
        "made": "2010-12-07",
        "provision": "Section 124(1) of the 1996 Act",
        "previous_amount": "£65,300",
        "commencement": "Comes into force on 1st February 2011.",
        "transitional": "Does not apply before 1st February 2011.",
        "caveat": None,
        "source_url": "https://www.legislation.gov.uk/uksi/2010/2926/made/data.xml",
        "xml_sha256": "9ddabadebfa743b2aaa7f4deb001e46db23b86b68de8c4181dc49bc635d78b97",
    }
    row.update(overrides)
    return row


def _run_pipeline(
    pipeline: Pipeline,
    query: str,
    *,
    as_at: date | None = None,
) -> tuple[list[Event], Any]:
    events: list[Event] = []
    outcome = pipeline.run(AskRequest(query=query, as_at=as_at), events.append)
    assert validate_order([e.type for e in events])
    return events, outcome


def _route_result(coord: str, temporal_hint: str | None) -> RouteResult:
    return RouteResult(
        status=RouteStatus.BOUNDED,
        next_action=NextAction.RETRIEVE_BOUNDED,
        coordinates=(Coordinate.parse(coord),),
        excluded=(),
        candidates=(),
        clarification=None,
        citations=(),
        citation_span=None,
        corrections=(),
        suggestions=(),
        repealed=False,
        temporal_hint=temporal_hint,
        citation_signal=True,
        source="grammar",
        index_snapshot=SNAPSHOT,
        latency_ns=1000,
        reason="exact",
        messages=(),
    )


def _real_pit() -> PitLimits:
    if not REAL_PIT_LIMITS.is_file():
        pytest.skip("artefacts/pit/limits.json not present")
    loaded = load_pit_limits(REAL_PIT_LIMITS, router_snapshot=SNAPSHOT)
    assert loaded is not None
    return loaded


# ---------------------------------------------------------------------------- date parsing


def test_parse_exact_date() -> None:
    assert parse_exact_date("As at 1 January 2012") == date(2012, 1, 1)
    assert parse_exact_date("on 1st February 2012") == date(2012, 2, 1)
    assert parse_exact_date("February 1, 2012") == date(2012, 2, 1)
    assert parse_exact_date("01/02/2012") == date(2012, 2, 1)
    assert parse_exact_date("2014-01-01") == date(2014, 1, 1)

    assert parse_exact_date(None) is None
    assert parse_exact_date("") is None
    assert parse_exact_date("As at 2012") is None
    assert parse_exact_date("in February 2012") is None
    assert parse_exact_date("between 1 January 2012 and 1 February 2012") is None


# ---------------------------------------------------------------------------- fail-closed loader


def test_load_pit_limits_fail_closed(tmp_path: Path) -> None:
    assert load_pit_limits(None, router_snapshot=SNAPSHOT) is None
    assert load_pit_limits(tmp_path / "missing.json", router_snapshot=SNAPSHOT) is None

    # Missing manifest
    no_manifest = tmp_path / "no_manifest" / "limits.json"
    no_manifest.parent.mkdir()
    no_manifest.write_text(json.dumps([_sample_row()]), encoding="utf-8")
    assert load_pit_limits(no_manifest, router_snapshot=SNAPSHOT) is None

    # Invalid JSON in limits.json
    bad_json_dir = tmp_path / "bad_json"
    bad_json_dir.mkdir()
    bad_bytes = b"{not json"
    (bad_json_dir / "limits.json").write_bytes(bad_bytes)
    (bad_json_dir / "pit-manifest.json").write_bytes(
        canonical_json(
            {
                "snapshot": SNAPSHOT,
                "files": {
                    "limits.json": {
                        "sha256": hashlib.sha256(bad_bytes).hexdigest(),
                        "size": len(bad_bytes),
                    }
                },
            }
        )
    )
    assert load_pit_limits(bad_json_dir / "limits.json", router_snapshot=SNAPSHOT) is None

    # Snapshot mismatch
    snap_path = _write_pit_artefact(tmp_path / "bad_snap", [_sample_row()], snapshot="2020-01-01")
    assert load_pit_limits(snap_path, router_snapshot=SNAPSHOT) is None

    # SHA-256 mismatch
    sha_path = _write_pit_artefact(tmp_path / "bad_sha", [_sample_row()], override_sha="0" * 64)
    assert load_pit_limits(sha_path, router_snapshot=SNAPSHOT) is None

    # Size mismatch
    size_path = _write_pit_artefact(tmp_path / "bad_size", [_sample_row()], override_size=999999)
    assert load_pit_limits(size_path, router_snapshot=SNAPSHOT) is None

    # Invalid row schema (non-legislation URL)
    bad_url = _write_pit_artefact(
        tmp_path / "bad_url", [_sample_row(source_url="https://example.com/bad")]
    )
    assert load_pit_limits(bad_url, router_snapshot=SNAPSHOT) is None

    # Overlapping intervals for the same coordinate
    overlap = _write_pit_artefact(
        tmp_path / "overlap",
        [
            _sample_row(valid_from="2011-02-01", valid_to="2012-06-01"),
            _sample_row(valid_from="2012-02-01", valid_to="2013-02-01"),
        ],
    )
    assert load_pit_limits(overlap, router_snapshot=SNAPSHOT) is None


def test_load_real_pit_limits() -> None:
    loaded = _real_pit()
    assert len(loaded.rows) == 48
    assert loaded.snapshot == SNAPSHOT


def test_app_load_pit_limits_env(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="chapterverse.app"):
        assert _load_pit_limits({}, router_snapshot=SNAPSHOT) is None
        assert any("PIT_LIMITS_PATH unset" in r.getMessage() for r in caplog.records)

        caplog.clear()
        missing = tmp_path / "missing" / "limits.json"
        assert _load_pit_limits({"PIT_LIMITS_PATH": str(missing)}, router_snapshot=SNAPSHOT) is None
        assert any("absent" in r.getMessage() for r in caplog.records)

        caplog.clear()
        bad = _write_pit_artefact(tmp_path / "bad", [_sample_row()], override_sha="f" * 64)
        assert _load_pit_limits({"PIT_LIMITS_PATH": str(bad)}, router_snapshot=SNAPSHOT) is None
        assert any("checksum mismatch" in r.getMessage() for r in caplog.records)

        caplog.clear()
        good = _write_pit_artefact(tmp_path / "good", [_sample_row()])
        loaded = _load_pit_limits({"PIT_LIMITS_PATH": str(good)}, router_snapshot=SNAPSHOT)
        assert loaded is not None
        assert len(loaded.rows) == 1
        assert any("pit limits on:" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------- pipeline behaviour


def test_pit_era_124_1_and_boundary_dates(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    pit = _real_pit()
    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=_NoCallRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
        pit_limits=pit,
    )

    # 2012-01-01 -> £68,400, SI 2010/2926
    events, outcome = _run_pipeline(pipeline, PIT_ERA_124_1)
    assert outcome.mode == "generated"
    assert outcome.fact is not None
    assert outcome.fact.calls == ()
    assert outcome.selection == "pit_limits"
    sources = next(e for e in events if isinstance(e, SourcesEvent))
    sentence = next(e for e in events if isinstance(e, SentenceEvent))
    receipt = next(e for e in events if isinstance(e, ReceiptEvent))
    done = next(e for e in events if isinstance(e, DoneEvent))
    assert isinstance(done, DoneEvent)
    assert len(sources.windows) == 1
    assert sources.windows[0].coordinate == "uk/uksi/2010/2926"
    assert sources.windows[0].source_url == "https://www.legislation.gov.uk/uksi/2010/2926/made"
    assert "£68,400" in sentence.sentence.text
    assert "SI 2010/2926" in sentence.sentence.text
    assert "2011-02-01" in sentence.sentence.text
    assert "2012-01-31" in sentence.sentence.text
    assert sentence.citations[0].url == "https://www.legislation.gov.uk/uksi/2010/2926/made"
    assert "SI 2010/2926" in sentence.citations[0].label
    assert receipt.fact.calls == ()

    # 2012-02-01 boundary -> £72,300, SI 2011/3006
    events_feb, outcome_feb = _run_pipeline(
        pipeline,
        "As at 1 February 2012, what was the maximum compensatory award under section 124 "
        "of the Employment Rights Act 1996?",
    )
    assert outcome_feb.mode == "generated"
    sent_feb = next(e for e in events_feb if isinstance(e, SentenceEvent))
    assert "£72,300" in sent_feb.sentence.text
    assert "SI 2011/3006" in sent_feb.sentence.text

    # 2014-01-01 -> £74,200, SI 2012/3007 + 52-week cap caveat (both s.124 and s.124(1ZA))
    for q in (
        (
            "As at 1 January 2014, what was the maximum compensatory award under section 124 "
            "of the Employment Rights Act 1996?"
        ),
        (
            "As at 1 January 2014, what was the maximum compensatory award under section 124(1ZA) "
            "of the Employment Rights Act 1996?"
        ),
    ):
        events_2014, outcome_2014 = _run_pipeline(pipeline, q)
        assert outcome_2014.mode == "generated"
        sent_2014 = next(e for e in events_2014 if isinstance(e, SentenceEvent))
        assert "£74,200" in sent_2014.sentence.text
        assert "SI 2012/3007" in sent_2014.sentence.text
        assert "52 weeks' pay" in sent_2014.sentence.text
        assert "SI 2013/1949" in sent_2014.sentence.text

    # Open-ended latest row (valid_to is None) -> 2026-04-06 gives £123,543, SI 2026/310
    events_2026, outcome_2026 = _run_pipeline(
        pipeline,
        "As at 6 April 2026, what was the maximum compensatory award under section 124 "
        "of the Employment Rights Act 1996?",
    )
    assert outcome_2026.mode == "generated"
    sent_2026 = next(e for e in events_2026 if isinstance(e, SentenceEvent))
    assert "£123,543" in sent_2026.sentence.text
    assert "SI 2026/310" in sent_2026.sentence.text

    # Explicit Request.as_at resolves via pit_limits
    events_param, outcome_param = _run_pipeline(
        pipeline,
        "What is the maximum compensatory award under section 124 "
        "of the Employment Rights Act 1996?",
        as_at=date(2012, 1, 1),
    )
    assert outcome_param.mode == "generated"
    sent_param = next(e for e in events_param if isinstance(e, SentenceEvent))
    assert "£68,400" in sent_param.sentence.text


def test_pit_s186_and_s227_and_fallbacks() -> None:
    pit = _real_pit()
    instruments = {
        "uk/ukpga/1996/18": {
            "record_type": "instrument",
            "coordinate": "uk/ukpga/1996/18",
            "title": "Employment Rights Act 1996",
            "version_date": "2026-07-29",
            "source_url": "https://www.legislation.gov.uk/ukpga/1996/18",
        },
        "uk/ukpga/2006/46": {
            "record_type": "instrument",
            "coordinate": "uk/ukpga/2006/46",
            "title": "Companies Act 2006",
            "version_date": "2026-07-29",
            "source_url": "https://www.legislation.gov.uk/ukpga/2006/46",
        },
    }
    store = _FakeInstrumentStore(instruments)
    policy = build_policy(JUDGE_PRESET_NAME)

    # s.186 and s.227 succeed deterministically
    for coord, expected_amt in (
        ("uk/ukpga/1996/18/s186", "£400"),
        ("uk/ukpga/1996/18/s227", "£400"),
    ):
        pipeline = Pipeline(
            router=cast("Router", _FakeRouteRouter(_route_result(coord, "As at 1 January 2012"))),
            store=store,
            rotation=cast("Any", _NoCallRotation()),
            policy=policy,
            pit_limits=pit,
        )
        events, outcome = _run_pipeline(pipeline, "As at 1 January 2012...")
        assert outcome.mode == "generated"
        sent = next(e for e in events if isinstance(e, SentenceEvent))
        assert expected_amt in sent.sentence.text
        assert "SI 2010/2926" in sent.sentence.text

    # Pre-2011, imprecise date, unrelated subsection s.124(5), uncovered CA 2006 s.382,
    # and pit_limits=None all fall through to HISTORY_NOT_HELD with a dated legislation.gov.uk URL.
    refusal_cases = [
        ("uk/ukpga/1996/18/s124", "As at 1 January 2010", pit, "2010-01-01"),
        ("uk/ukpga/1996/18/s124", "As at 2012", pit, "2012-01-01"),
        ("uk/ukpga/1996/18/s124/5", "As at 1 January 2012", pit, "2012-01-01"),
        ("uk/ukpga/2006/46/s382", "As at 1 January 2012", pit, "2012-01-01"),
        ("uk/ukpga/1996/18/s124", "As at 1 January 2012", None, "2012-01-01"),
    ]
    for coord, hint, active_pit, expected_date_url in refusal_cases:
        pipeline = Pipeline(
            router=cast("Router", _FakeRouteRouter(_route_result(coord, hint))),
            store=store,
            rotation=cast("Any", _NoCallRotation()),
            policy=policy,
            pit_limits=active_pit,
        )
        events, outcome = _run_pipeline(pipeline, f"{hint} question")
        assert outcome.mode == "refusal"
        refusal = next(e for e in events if isinstance(e, RefusalEvent))
        assert refusal.kind == "HISTORY_NOT_HELD"
        assert refusal.link is not None
        assert expected_date_url in refusal.link


def test_local_app_pit_era_124_1_repeated_20x() -> None:
    store_dir = REPO / "artefacts" / "store"
    data_root = Path(
        os.environ.get("LEGAL_DATA_ROOT", "/Users/abdiel/Code/legal-rag-router/data")
    ).expanduser()
    if not (
        store_dir.is_dir()
        and (data_root / "index").is_dir()
        and (data_root / "concepts").is_dir()
        and REAL_PIT_LIMITS.is_file()
    ):
        pytest.skip("local artefacts (store, LEGAL_DATA_ROOT, pit) not present")

    env = {
        "STORE_DIR": str(store_dir),
        "LEGAL_DATA_ROOT": str(data_root),
        "PIT_LIMITS_PATH": str(REAL_PIT_LIMITS),
        "SYMSPELL_DICT": str(REPO / "artefacts" / "symspell" / "dictionary.txt"),
        "NO_DENSE_OVERLAP": "1",
        "CAP_SALT": "test-salt-" + "x" * 32,
        "DAILY_ASK_CAP": "0",
        "RATE_LIMIT_BURST": "30",
        "RATE_LIMIT_PER_MINUTE": "600",
    }
    runtime = build_runtime(env)
    try:
        app = create_app(runtime)
        with TestClient(app) as client:
            # One untimed warmup to clear FastAPI/Starlette first-request setup
            warm = client.post("/api/ask", json={"query": PIT_ERA_124_1})
            assert warm.status_code == 200

            texts: list[str] = []
            answer_ids: list[str] = []
            timings: list[float] = []
            for _ in range(20):
                resp = client.post("/api/ask", json={"query": PIT_ERA_124_1})
                assert resp.status_code == 200
                parsed = parse_sse(resp.content)
                assert validate_order([kind for kind, _, _ in parsed])
                by_type = {kind: data for kind, data, _ in parsed}
                assert by_type["receipt"]["fact"]["calls"] == []
                assert by_type["receipt"]["fact"]["mode"] == "generated"
                texts.append(by_type["sentence"]["sentence"]["text"])
                answer_ids.append(by_type["receipt"]["answer_id"])
                timings.append(float(by_type["done"]["timings"]["total_ms"]))

            assert len(set(texts)) == 1
            assert len(set(answer_ids)) == 1
            assert "£68,400" in texts[0]
            assert "SI 2010/2926" in texts[0]
            assert max(timings) < 20.0, f"slowest PIT total_ms={max(timings):.2f}"
    finally:
        runtime.shutdown()
