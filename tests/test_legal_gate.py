"""Unit tests for the legal battery gate, seal, rubric, and recorder."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legal_rag_router import Router  # noqa: E402

from chapterverse.audit.policy import build_policy  # noqa: E402
from chapterverse.contracts import (  # noqa: E402
    RenderedCitation,
    SentenceEvent,
    SentenceOut,
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
