"""Unit tests for the legal battery gate, seal, rubric, and recorder."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legal_rag_router import Router  # noqa: E402

import chapterverse.app  # noqa: E402
from chapterverse.audit.policy import build_policy  # noqa: E402
from chapterverse.contracts import (  # noqa: E402
    AnswerFactObject,
    DoneEvent,
    ProviderCall,
    ReceiptEvent,
    RenderedCitation,
    RouteEvent,
    SentenceEvent,
    SentenceOut,
    SourcesEvent,
    TemporalStatus,
    Timings,
    Window,
    sha256_hex,
)
from chapterverse.pipeline import Pipeline  # noqa: E402
from chapterverse.providers.presets import JUDGE_PRESET_NAME  # noqa: E402
from eval.legal_gate import (  # noqa: E402
    DEFAULT_BATTERY,
    DEFAULT_SEAL,
    HTTP_OK,
    evaluate_answer,
    is_descendant_or_equal,
    load_battery,
    main,
    run_recorder,
    run_retrieval_gate,
    run_rubric,
    verify_seal,
)
from eval.run import NoModelRotation  # noqa: E402


# ============================================================================ 1. Battery validation
def test_battery_file_validates() -> None:
    assert DEFAULT_BATTERY.is_file(), f"{DEFAULT_BATTERY} does not exist"
    rows = load_battery(DEFAULT_BATTERY)

    assert len(rows) == 40, f"Expected 40 rows, got {len(rows)}"

    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    dev_rows = [r for r in rows if r["split"] == "dev"]
    test_rows = [r for r in rows if r["split"] == "test"]
    assert len(dev_rows) == 20, f"Expected 20 dev rows, got {len(dev_rows)}"
    assert len(test_rows) == 20, f"Expected 20 test rows, got {len(test_rows)}"

    for row in rows:
        assert row["split"] in ("dev", "test")
        assert row["question"].strip()
        inst = row["instrument"].strip()
        assert inst.startswith("uk/ukpga/")

        must_reach = row["must_reach"]
        assert len(must_reach) >= 1, f"Row {row['id']} has empty must_reach"
        for m in must_reach:
            assert m.startswith(inst + "/"), (
                f"must_reach item {m} not inside instrument {inst} in {row['id']}"
            )

        for s in row["should_reach"]:
            assert s.startswith(inst + "/"), (
                f"should_reach item {s} not inside instrument {inst} in {row['id']}"
            )

        checks = row["answer_checks"]
        if checks is not None:
            assert "required_citations" in checks
            assert "forbidden_phrases" in checks
            assert "max_abstentions" in checks
            assert "no_citations_on_abstention" in checks
            assert "no_duplicate_sentences" in checks


# ============================================================================ 2. Seal check
def test_seal_check_validates() -> None:
    assert DEFAULT_SEAL.is_file()
    ok, note = verify_seal(DEFAULT_BATTERY, DEFAULT_SEAL)
    assert ok is True, f"Seal check failed: {note}"


def test_seal_check_tampered_test_row_refused(tmp_path: Path) -> None:
    rows = load_battery(DEFAULT_BATTERY)
    # Tamper with a test row
    for r in rows:
        if r["split"] == "test":
            r["question"] = r["question"] + " (tampered)"
            break

    tampered_battery = tmp_path / "legal.jsonl"
    with tampered_battery.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    ok, note = verify_seal(tampered_battery, DEFAULT_SEAL)
    assert ok is False
    assert "seal sha256 mismatch" in note

    # Gate refuses test split on tampered battery
    rc = main(
        [
            "--split",
            "test",
            "--sealed-test",
            "--battery",
            str(tampered_battery),
            "--seal",
            str(DEFAULT_SEAL),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 1


def test_gate_refuses_test_without_sealed_test_flag(tmp_path: Path) -> None:
    rc = main(
        [
            "--split",
            "test",
            "--battery",
            str(DEFAULT_BATTERY),
            "--seal",
            str(DEFAULT_SEAL),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 1


# ============================================================================ 3. Descendant match
def test_descendant_match_rule() -> None:
    target = "uk/ukpga/1980/58/s2"
    assert is_descendant_or_equal(target, target) is True
    assert is_descendant_or_equal("uk/ukpga/1980/58/s2/1", target) is True
    assert is_descendant_or_equal("uk/ukpga/1980/58/s2/1/a", target) is True
    assert is_descendant_or_equal("uk/ukpga/1980/58/s2/1/a/i", target) is True

    # Section with shared prefix but distinct identity
    assert is_descendant_or_equal("uk/ukpga/1980/58/s2A", target) is False
    assert is_descendant_or_equal("uk/ukpga/1980/58/s20", target) is False
    assert is_descendant_or_equal("uk/ukpga/1980/58/s20/1", target) is False

    # Parent or ancestor
    assert is_descendant_or_equal("uk/ukpga/1980/58", target) is False
    assert is_descendant_or_equal("uk/ukpga", target) is False

    # Different section / instrument
    assert is_descendant_or_equal("uk/ukpga/1980/58/s5", target) is False
    assert is_descendant_or_equal("uk/ukpga/2006/46/s2", target) is False


# ============================================================================ 4. Rubric synthetic
def test_rubric_synthetic_events_pass_and_fail() -> None:
    policy = build_policy(JUDGE_PRESET_NAME)
    refusal_text = policy.refusal_text

    base_row = {
        "id": "TEST01",
        "split": "dev",
        "question": "Sample question",
        "instrument": "uk/ukpga/1980/58",
        "must_reach": ["uk/ukpga/1980/58/s2"],
        "should_reach": [],
        "answer_checks": {
            "required_citations": ["uk/ukpga/1980/58/s2"],
            "credit_citations": ["uk/ukpga/1980/58/s11"],
            "forbidden_phrases": ["the passages", "provided passages"],
            "max_abstentions": 1,
            "no_citations_on_abstention": True,
            "no_duplicate_sentences": True,
        },
    }

    # Baseline passing events
    def make_pass_events() -> list[Any]:
        return [
            SentenceEvent(
                sentence=SentenceOut(
                    index=0,
                    text="Under section 2, the action must be brought in six years.",
                    verdict="supported",
                    passage_ids=("P1",),
                    reasons=("GROUNDED",),
                    rollbacks=0,
                ),
                citations=(
                    RenderedCitation(
                        passage_id="P1",
                        coordinate="uk/ukpga/1980/58/s2/1",
                        label="s.2(1)",
                        url="http://example.com",
                    ),
                    RenderedCitation(
                        passage_id="P1",
                        coordinate="uk/ukpga/1980/58/s11",
                        label="s.11",
                        url="http://example.com",
                    ),
                ),
            )
        ]

    res_pass = evaluate_answer(base_row, make_pass_events(), refusal_text)
    assert res_pass["passed"] is True
    assert res_pass["failure_reasons"] == []
    assert res_pass["credit_hits"] == ["uk/ukpga/1980/58/s11"]

    # 1. required_citations FAIL
    ev_fail_cits = [
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text="Some text without required citation.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1980/58/s5",
                    label="s.5",
                    url="http://example.com",
                ),
            ),
        )
    ]
    res_fail_cits = evaluate_answer(base_row, ev_fail_cits, refusal_text)
    assert res_fail_cits["passed"] is False
    assert any("missing required citations" in r for r in res_fail_cits["failure_reasons"])

    # 2. forbidden_phrases FAIL
    ev_fail_forbid = [
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text="According to the passages provided, the time limit is six years.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1980/58/s2",
                    label="s.2",
                    url="http://example.com",
                ),
            ),
        )
    ]
    res_fail_forbid = evaluate_answer(base_row, ev_fail_forbid, refusal_text)
    assert res_fail_forbid["passed"] is False
    assert any("forbidden phrases found" in r for r in res_fail_forbid["failure_reasons"])

    # 3. max_abstentions FAIL (2 abstentions > max 1)
    ev_fail_absten = [
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text=refusal_text,
                verdict="refused",
                passage_ids=(),
                reasons=(),
                rollbacks=0,
            ),
            citations=(),
        ),
        SentenceEvent(
            sentence=SentenceOut(
                index=1,
                text=refusal_text,
                verdict="refused",
                passage_ids=(),
                reasons=(),
                rollbacks=0,
            ),
            citations=(),
        ),
    ]
    res_fail_absten = evaluate_answer(base_row, ev_fail_absten, refusal_text)
    assert res_fail_absten["passed"] is False
    assert any("too many abstentions" in r for r in res_fail_absten["failure_reasons"])

    # 4. no_citations_on_abstention FAIL
    ev_fail_cits_absten = [
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text=refusal_text,
                verdict="refused",
                passage_ids=("P1",),
                reasons=(),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1980/58/s2",
                    label="s.2",
                    url="http://example.com",
                ),
            ),
        )
    ]
    res_fail_cits_absten = evaluate_answer(base_row, ev_fail_cits_absten, refusal_text)
    assert res_fail_cits_absten["passed"] is False
    assert any(
        "abstention sentence has citations" in r for r in res_fail_cits_absten["failure_reasons"]
    )

    # 5. no_duplicate_sentences FAIL
    ev_fail_dup = [
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text="The limitation period is six years.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1980/58/s2",
                    label="s.2",
                    url="http://example.com",
                ),
            ),
        ),
        SentenceEvent(
            sentence=SentenceOut(
                index=1,
                text="The limitation period is six years.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1980/58/s2",
                    label="s.2",
                    url="http://example.com",
                ),
            ),
        ),
    ]
    res_fail_dup = evaluate_answer(base_row, ev_fail_dup, refusal_text)
    assert res_fail_dup["passed"] is False
    assert any("duplicate sentences found" in r for r in res_fail_dup["failure_reasons"])

    # Run rubric helper check
    rubric_report = run_rubric([base_row], [{"id": "TEST01", "events": make_pass_events()}])
    assert rubric_report["total_evaluated"] == 1
    assert rubric_report["passed"] == 1
    assert rubric_report["failed"] == 0


# ============================================================================ 5. Recorder fake
def test_recorder_with_fake_transport(tmp_path: Path) -> None:
    def fake_handler(_request: httpx.Request) -> httpx.Response:
        route_data = {
            "status": "ROUTE_BOUNDED",
            "next_action": "answer",
            "coordinates": ["uk/ukpga/1980/58"],
            "corrections": [],
            "messages": [],
            "index_snapshot": "2026-09-28",
        }
        sources_data = {
            "windows": [
                {
                    "passage_id": "P1",
                    "coordinate": "uk/ukpga/1980/58/s2",
                    "heading": "s.2",
                    "text": "text",
                    "status": "IN_FORCE",
                    "version_date": None,
                    "source_url": "http://example.com",
                }
            ]
        }
        done_data = {
            "timings": {
                "first_source_ms": 1.0,
                "first_sentence_ms": None,
                "total_ms": 5.0,
            }
        }
        sse_lines = [
            f"event: route\ndata: {json.dumps(route_data)}\n\n",
            f"event: sources\ndata: {json.dumps(sources_data)}\n\n",
            f"event: done\ndata: {json.dumps(done_data)}\n\n",
        ]
        return httpx.Response(HTTP_OK, content="".join(sse_lines).encode("utf-8"))

    transport = httpx.MockTransport(fake_handler)
    test_rows = [
        {
            "id": "L01",
            "question": "What is the limitation period?",
            "answer_checks": {"required_citations": ["uk/ukpga/1980/58/s2"]},
        }
    ]
    out_file = tmp_path / "recorded_answers.jsonl"
    rc = run_recorder(
        test_rows,
        out_file,
        target="live",
        pace=0.0,
        transport=transport,
    )
    assert rc == 0
    assert out_file.is_file()

    recorded = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
    assert len(recorded) == 1
    assert recorded[0]["id"] == "L01"
    event_types = [e["type"] for e in recorded[0]["events"]]
    assert event_types == ["route", "sources", "done"]


# ============================================================================ 6. Fixture pipeline
def test_retrieval_gate_fixture_pipeline(fixture_router: Router, fixture_text_store: Any) -> None:
    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=NoModelRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
    )

    rows = [
        {
            "id": "FX01",
            "question": "What is the limit of compensatory award under Employment Rights Act 1996?",
            "must_reach": ["uk/ukpga/1996/18/s124"],
            "should_reach": ["uk/ukpga/1996/18/s123"],
            "note": "",
        }
    ]

    report = run_retrieval_gate(rows, pipeline)
    assert report["totals"]["rows_total"] == 1
    assert report["totals"]["rows_passed"] == 1
    assert report["totals"]["must_reach_recall"] == 1.0
    r0 = report["rows"][0]
    assert r0["passed"] is True
    assert r0["route_status"] == "ROUTE_BOUNDED"
    assert "uk/ukpga/1996/18" in r0["bound_coordinates"]
    assert any(is_descendant_or_equal(w, "uk/ukpga/1996/18/s124") for w in r0["windows_sent"])


# ============================================================================ 7. Local recorder
def _write_test_battery(path: Path) -> None:
    rows = [
        {
            "id": "L01",
            "split": "dev",
            "question": "Tort limitation period?",
            "instrument": "uk/ukpga/1980/58",
            "must_reach": ["uk/ukpga/1980/58/s2"],
            "should_reach": [],
            "answer_checks": {
                "required_citations": ["uk/ukpga/1980/58/s2"],
                "credit_citations": [],
                "forbidden_phrases": ["provided passages"],
                "max_abstentions": 1,
                "no_citations_on_abstention": True,
                "no_duplicate_sentences": True,
            },
        },
        {
            "id": "L02",
            "split": "test",
            "question": "Contract limitation period?",
            "instrument": "uk/ukpga/1980/58",
            "must_reach": ["uk/ukpga/1980/58/s5"],
            "should_reach": [],
            "answer_checks": None,
        },
        {
            "id": "L07",
            "split": "dev",
            "question": "Director duty wording?",
            "instrument": "uk/ukpga/2006/46",
            "must_reach": ["uk/ukpga/2006/46/s172/1/a"],
            "should_reach": [],
            "answer_checks": {
                "required_citations": ["uk/ukpga/2006/46/s172/1/a"],
                "credit_citations": [],
                "forbidden_phrases": ["provided passages"],
                "max_abstentions": 1,
                "no_citations_on_abstention": True,
                "no_duplicate_sentences": True,
            },
        },
        {
            "id": "L09",
            "split": "dev",
            "question": "Short-term right to reject?",
            "instrument": "uk/ukpga/2015/15",
            "must_reach": ["uk/ukpga/2015/15/s22"],
            "should_reach": [],
            "answer_checks": {
                "required_citations": ["uk/ukpga/2015/15/s22"],
                "credit_citations": [],
                "forbidden_phrases": ["provided passages"],
                "max_abstentions": 1,
                "no_citations_on_abstention": True,
                "no_duplicate_sentences": True,
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class _FakeRotation:
    def available(self, purpose: str | None = None) -> list[str]:
        if purpose == "judge":
            return ["modal"]
        return ["groq", "cloudflare"]


def _sample_events(coord: str, query: str, *, with_receipt: bool) -> list[Any]:
    inst = "/".join(coord.split("/")[:4])
    events: list[Any] = [
        RouteEvent(
            status="ROUTE_BOUNDED",
            next_action="answer",
            coordinates=(coord,),
            corrections=(),
            messages=(),
            index_snapshot="2026-09-28",
        ),
        SourcesEvent(
            windows=(
                Window(
                    passage_id="P1",
                    coordinate=coord,
                    heading="s",
                    text="Statutory rule.",
                    status=TemporalStatus.IN_FORCE,
                    version_date=None,
                    source_url="http://example.com",
                ),
            )
        ),
        SentenceEvent(
            sentence=SentenceOut(
                index=0,
                text="The statutory time limit is six years.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate=coord,
                    label="s",
                    url="http://example.com",
                ),
            ),
        ),
    ]
    if with_receipt:
        fact = AnswerFactObject(
            query_sha256=sha256_hex(query),
            route_status="ROUTE_BOUNDED",
            coordinates=(coord,),
            passages={"P1": coord},
            version_dates={inst: None},
            spans={},
            sentences=(events[2].sentence,),
            corpus_id="corpus-1",
            calls=(
                ProviderCall(
                    provider="groq",
                    model_id="qwen/qwen3-32b",
                    quantisation=None,
                    purpose="generate",
                    prompt_tokens=100,
                    completion_tokens=20,
                    latency_ms=42.34,
                ),
                ProviderCall(
                    provider="modal",
                    model_id="qwen3-32b",
                    quantisation="fp8",
                    purpose="judge",
                    prompt_tokens=80,
                    completion_tokens=10,
                    latency_ms=19.86,
                ),
            ),
            policy_hash="policy-1",
            mode="generated",
        )
        events.append(ReceiptEvent(answer_id=fact.answer_id, fact=fact))
    events.append(
        DoneEvent(timings=Timings(first_source_ms=1.0, first_sentence_ms=2.0, total_ms=5.0))
    )
    return events


def test_local_recorder_uses_build_runtime_drops_db_and_writes_rubric_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    battery_path = tmp_path / "legal.jsonl"
    _write_test_battery(battery_path)

    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret-pass@localhost/cv")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-value-never-print")

    captured_envs: list[dict[str, str]] = []
    seen_requests: list[Any] = []
    shutdown_called = False

    class _FakePipeline:
        rotation = _FakeRotation()

        def run(self, request: Any, emit: Any) -> None:
            seen_requests.append(request)
            coord = (
                "uk/ukpga/1980/58/s2"
                if "Tort" in request.query
                else (
                    "uk/ukpga/2006/46/s172/1/a"
                    if "Director" in request.query
                    else "uk/ukpga/2015/15/s22"
                )
            )
            for ev in _sample_events(coord, request.query, with_receipt=True):
                emit(ev)

    class _FakeRuntime:
        pipeline = _FakePipeline()

        def shutdown(self) -> None:
            nonlocal shutdown_called
            shutdown_called = True

    def fake_build_runtime(environ: Any = None) -> _FakeRuntime:
        assert environ is not None
        captured_envs.append(dict(environ))
        return _FakeRuntime()

    monkeypatch.setattr(chapterverse.app, "build_runtime", fake_build_runtime)

    out_dir = tmp_path / "rec_out"
    rc = main(
        [
            "--record",
            "--target",
            "local",
            "--pace",
            "0",
            "--battery",
            str(battery_path),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert shutdown_called is True
    assert len(captured_envs) == 1
    assert "DATABASE_URL" not in captured_envs[0]
    assert os.environ["DATABASE_URL"] == "postgres://user:secret-pass@localhost/cv"

    stdout = capsys.readouterr().out
    assert "groq, cloudflare" in stdout
    assert "modal" in stdout
    assert "gsk-secret-value-never-print" not in stdout
    assert "secret-pass" not in stdout

    assert len(seen_requests) == 3
    for req in seen_requests:
        assert req.client_key is None
        assert req.net_class == "other"

    answers_path = out_dir / "answers.jsonl"
    assert answers_path.is_file()
    recorded = [json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in recorded] == ["L01", "L07", "L09"]
    for rec in recorded:
        assert isinstance(rec["elapsed_ms"], float)
        assert rec["provider_calls"] == [
            {"provider": "groq", "purpose": "generate", "latency_ms": 42.3},
            {"provider": "modal", "purpose": "judge", "latency_ms": 19.9},
        ]

    rubric_dir = tmp_path / "rubric_out"
    rc_rubric = main(
        [
            "--answers",
            str(answers_path),
            "--battery",
            str(battery_path),
            "--out",
            str(rubric_dir),
        ]
    )
    assert rc_rubric == 0
    rubric_data = json.loads((rubric_dir / "answers-rubric.json").read_text(encoding="utf-8"))
    assert rubric_data["total_evaluated"] == 3
    assert rubric_data["passed"] == 3
    assert rubric_data["failed"] == 0


def test_rows_option_works_for_record_and_answers_and_omits_calls_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battery_path = tmp_path / "legal.jsonl"
    _write_test_battery(battery_path)

    seen_queries: list[str] = []

    class _NoReceiptPipeline:
        rotation = _FakeRotation()

        def run(self, request: Any, emit: Any) -> None:
            seen_queries.append(request.query)
            coord = (
                "uk/ukpga/1980/58/s2" if "Tort" in request.query else "uk/ukpga/2006/46/s172/1/a"
            )
            for ev in _sample_events(coord, request.query, with_receipt=False):
                emit(ev)

    class _FakeRuntime:
        pipeline = _NoReceiptPipeline()

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(chapterverse.app, "build_runtime", lambda _env=None: _FakeRuntime())

    answers_path = tmp_path / "subset.jsonl"
    rc = main(
        [
            "--record",
            "--target",
            "local",
            "--rows",
            "L01,L07",
            "--pace",
            "0",
            "--battery",
            str(battery_path),
            "--answers",
            str(answers_path),
            "--out",
            str(tmp_path / "rec"),
        ]
    )
    assert rc == 0
    assert seen_queries == ["Tort limitation period?", "Director duty wording?"]

    recorded = [json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in recorded] == ["L01", "L07"]
    for rec in recorded:
        assert "elapsed_ms" in rec
        assert "provider_calls" not in rec

    # --rows also filters --answers rubric scoring
    rubric_dir = tmp_path / "rubric_subset"
    rc_rubric = main(
        [
            "--answers",
            str(answers_path),
            "--rows",
            "L07",
            "--battery",
            str(battery_path),
            "--out",
            str(rubric_dir),
        ]
    )
    assert rc_rubric == 0
    rubric_data = json.loads((rubric_dir / "answers-rubric.json").read_text(encoding="utf-8"))
    assert rubric_data["total_evaluated"] == 1
    assert rubric_data["passed"] == 1
    assert [r["id"] for r in rubric_data["results"]] == ["L07"]

    # --rows can select a row without answer_checks (e.g. L02) during recording
    seen_queries.clear()
    l02_path = tmp_path / "l02.jsonl"
    rc_l02 = main(
        [
            "--record",
            "--target",
            "local",
            "--rows",
            "L02",
            "--pace",
            "0",
            "--battery",
            str(battery_path),
            "--answers",
            str(l02_path),
            "--out",
            str(tmp_path / "rec_l02"),
        ]
    )
    assert rc_l02 == 0
    assert seen_queries == ["Contract limitation period?"]
