"""Unit tests for eval.legal_answers."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chapterverse.app  # noqa: E402
from chapterverse.contracts import (  # noqa: E402
    AnswerFactObject,
    DoneEvent,
    NoticeEvent,
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
    canonical_json,
    sha256_hex,
    to_jsonable,
)
from chapterverse.templates import providers_unavailable_notice  # noqa: E402
from eval.legal_answers import (  # noqa: E402
    ANSWER_TEXT_LIMIT,
    build_answers_env,
    default_label,
    main,
    run_legal_answers,
)


def _write_sample_battery_and_seal(tmp_path: Path) -> tuple[Path, Path]:
    rows: list[dict[str, Any]] = [
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
            "note": "",
        },
        {
            "id": "L03",
            "split": "dev",
            "question": "Personal injury limitation?",
            "instrument": "uk/ukpga/1980/58",
            "must_reach": ["uk/ukpga/1980/58/s11"],
            "should_reach": [],
            "answer_checks": None,
            "note": "",
        },
        {
            "id": "L41",
            "split": "dev",
            "question": "Annual leave entitlement?",
            "instrument": "uk/uksi/1998/1833",
            "must_reach": ["uk/uksi/1998/1833/reg13"],
            "should_reach": [],
            "answer_checks": {
                "required_citations": ["uk/uksi/1998/1833/reg13"],
                "credit_citations": [],
                "forbidden_phrases": ["provided passages"],
                "max_abstentions": 1,
                "no_citations_on_abstention": True,
                "no_duplicate_sentences": True,
            },
            "note": "",
        },
        {
            "id": "L02",
            "split": "test",
            "question": "Contract limitation?",
            "instrument": "uk/ukpga/1980/58",
            "must_reach": ["uk/ukpga/1980/58/s5"],
            "should_reach": [],
            "answer_checks": {
                "required_citations": ["uk/ukpga/1980/58/s5"],
                "credit_citations": [],
                "forbidden_phrases": ["provided passages"],
                "max_abstentions": 1,
                "no_citations_on_abstention": True,
                "no_duplicate_sentences": True,
            },
            "note": "",
        },
    ]
    battery_path = tmp_path / "legal.jsonl"
    battery_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    test_rows = sorted([r for r in rows if r["split"] == "test"], key=lambda r: str(r["id"]))
    sha = hashlib.sha256(canonical_json(test_rows)).hexdigest()
    counts = {"dev": 3, "test": 1}
    seal_path = tmp_path / "SEAL.json"
    seal_path.write_text(
        json.dumps({"sha256": sha, "runs": 0, "row_counts": counts, "splits": counts}) + "\n",
        encoding="utf-8",
    )
    return battery_path, seal_path


def test_build_answers_env_removes_database_and_modal_keys() -> None:
    raw_env = {
        "DATABASE_URL": "postgres://user:pass@localhost/cv",
        "MODAL_URL": "https://modal.example.com",
        "MODAL_API_KEY": "modal-secret",
        "MODAL_MASK_PROCESSOR": "/nonexistent/mask.txt",
        "MODAL_TOKENIZER_JSON": "/nonexistent/tokenizer.json",
        "MODAL_CUSTOM_FLAG": "1",
        "GROQ_API_KEY": "gsk-test",
        "CLOUDFLARE_AI_WORKER_TOKEN": "cf-test",
        "CLOUDFLARE_ACCOUNT_ID": "acct-test",
        "GEN_PROVIDERS": "groq,cloudflare,modal",
        "JUDGE_PROVIDERS": "modal",
    }
    clean = build_answers_env(raw_env)
    assert "DATABASE_URL" not in clean
    for key in clean:
        assert not key.startswith("MODAL_")
    assert clean["GEN_PROVIDERS"] == "groq,cloudflare"
    assert clean["JUDGE_PROVIDERS"] == "groq,cloudflare"
    assert clean["GROQ_API_KEY"] == "gsk-test"
    assert clean["CLOUDFLARE_AI_WORKER_TOKEN"] == "cf-test"
    assert clean["CLOUDFLARE_ACCOUNT_ID"] == "acct-test"

    # Unset GEN_PROVIDERS / JUDGE_PROVIDERS defaults to Groq and Cloudflare only
    clean_defaults = build_answers_env({"GROQ_API_KEY": "gsk-test"})
    assert clean_defaults["GEN_PROVIDERS"] == "groq,cloudflare"
    assert clean_defaults["JUDGE_PROVIDERS"] == "groq,cloudflare"


def test_legal_answers_passes_clean_env_to_build_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battery_path, seal_path = _write_sample_battery_and_seal(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/cv")
    monkeypatch.setenv("MODAL_URL", "https://modal.example.com")
    monkeypatch.setenv("MODAL_API_KEY", "modal-secret")
    monkeypatch.setenv("MODAL_MASK_PROCESSOR", str(tmp_path / "mask.txt"))
    monkeypatch.setenv("MODAL_TOKENIZER_JSON", str(tmp_path / "tok.json"))
    monkeypatch.setenv("JUDGE_PROVIDERS", "modal")

    captured_envs: list[dict[str, str]] = []

    class _FakeRotation:
        def available(self, purpose: str | None = None) -> list[str]:
            del purpose
            return ["groq", "cloudflare"]

    class _FakePipeline:
        rotation = _FakeRotation()

        def run(self, request: Any, emit: Any) -> None:
            coord = "uk/ukpga/1980/58/s2"
            emit(
                RouteEvent(
                    status="ROUTE_BOUNDED",
                    next_action="answer",
                    coordinates=(coord,),
                    corrections=(),
                    messages=(),
                    index_snapshot="2026-09-28",
                )
            )
            emit(
                SourcesEvent(
                    windows=(
                        Window(
                            passage_id="P1",
                            coordinate=coord,
                            heading="s.2",
                            text="Six years.",
                            status=TemporalStatus.IN_FORCE,
                            version_date=None,
                            source_url="http://example.com",
                        ),
                    )
                )
            )
            emit(
                SentenceEvent(
                    sentence=SentenceOut(
                        index=0,
                        text="You have six years to bring an action in tort.",
                        verdict="supported",
                        passage_ids=("P1",),
                        reasons=("GROUNDED",),
                        rollbacks=0,
                    ),
                    citations=(
                        RenderedCitation(
                            passage_id="P1",
                            coordinate=coord,
                            label="s.2",
                            url="http://example.com",
                        ),
                    ),
                )
            )
            emit(
                DoneEvent(timings=Timings(first_source_ms=1.0, first_sentence_ms=2.0, total_ms=3.0))
            )

    class _FakeRuntime:
        pipeline = _FakePipeline()

        def shutdown(self) -> None:
            pass

    def fake_build_runtime(environ: Any = None) -> _FakeRuntime:
        assert environ is not None
        captured_envs.append(dict(environ))
        return _FakeRuntime()

    monkeypatch.setattr(chapterverse.app, "build_runtime", fake_build_runtime)

    out_dir = tmp_path / "reports"
    rc, _ = run_legal_answers(
        split="dev",
        battery_path=battery_path,
        seal_path=seal_path,
        row_ids=["L01"],
        pace=0.0,
        label="testlbl",
        out_dir=out_dir,
        utc_date="2026-11-25",
    )
    assert rc == 0
    assert len(captured_envs) == 1
    env_used = captured_envs[0]
    assert "DATABASE_URL" not in env_used
    for key in env_used:
        assert not key.startswith("MODAL_")
    assert env_used["GEN_PROVIDERS"] == "groq,cloudflare"
    assert env_used["JUDGE_PROVIDERS"] == "groq,cloudflare"


def test_legal_answers_refuses_test_without_seal(tmp_path: Path) -> None:
    battery_path, seal_path = _write_sample_battery_and_seal(tmp_path)
    out_dir = tmp_path / "reports"

    # Refuses without --sealed-test
    rc_no_flag = main(
        [
            "--split",
            "test",
            "--battery",
            str(battery_path),
            "--seal",
            str(seal_path),
            "--out",
            str(out_dir),
        ]
    )
    assert rc_no_flag == 1
    assert not out_dir.exists()

    # Refuses when seal mismatches
    seal_path.write_text(
        json.dumps({"sha256": "0" * 64, "row_counts": {"dev": 3, "test": 1}}) + "\n",
        encoding="utf-8",
    )
    rc_bad_seal = main(
        [
            "--split",
            "test",
            "--sealed-test",
            "--battery",
            str(battery_path),
            "--seal",
            str(seal_path),
            "--out",
            str(out_dir),
        ]
    )
    assert rc_bad_seal == 1
    assert not out_dir.exists()


def test_default_label_reads_git_head_ref(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/sprint-5\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "sprint-5").write_text(
        "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8"
    )
    assert default_label(git_dir) == "abcdef1"


def _make_fake_recorder(recorded_calls: list[dict[str, Any]]) -> Callable[..., int]:
    def fake_recorder(
        rows: list[dict[str, Any]],
        out_path: Path,
        *,
        target: str = "local",
        pace: float = 20.0,
        row_ids: Any = None,
        environ: Any = None,
    ) -> int:
        recorded_calls.append(
            {
                "ids": [r["id"] for r in rows],
                "target": target,
                "pace": pace,
                "row_ids": row_ids,
                "environ": dict(environ or {}),
            }
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for r in rows:
            rid = r["id"]
            if rid == "L01":
                coord = "uk/ukpga/1980/58/s2"
                events: list[Any] = [
                    RouteEvent(
                        status="ROUTE_BOUNDED",
                        next_action="answer",
                        coordinates=(coord,),
                        corrections=(),
                        messages=(),
                        index_snapshot="2026-09-28",
                    ),
                    SentenceEvent(
                        sentence=SentenceOut(
                            index=0,
                            text="A" * 350,
                            verdict="supported",
                            passage_ids=("P1",),
                            reasons=("GROUNDED",),
                            rollbacks=0,
                        ),
                        citations=(
                            RenderedCitation(
                                passage_id="P1",
                                coordinate=coord,
                                label="s.2",
                                url="http://example.com",
                            ),
                        ),
                    ),
                    providers_unavailable_notice(),
                    ReceiptEvent(
                        answer_id="aid-1",
                        fact=AnswerFactObject(
                            query_sha256=sha256_hex(r["question"]),
                            route_status="ROUTE_BOUNDED",
                            coordinates=(coord,),
                            passages={"P1": coord},
                            version_dates={"uk/ukpga/1980/58": None},
                            spans={},
                            sentences=(),
                            corpus_id="c1",
                            calls=(
                                ProviderCall("groq", "qwen", None, "generate", 10, 5, 11.2),
                                ProviderCall("cloudflare", "qwen", None, "judge", 10, 2, 22.4),
                            ),
                            policy_hash="p1",
                            mode="generated",
                        ),
                    ),
                    DoneEvent(timings=Timings(1.0, 2.0, 33.6)),
                ]
                elapsed = 33.6
            else:
                events = [
                    RouteEvent(
                        status="ROUTE_BOUNDED",
                        next_action="answer",
                        coordinates=("uk/uksi/1998/1833",),
                        corrections=(),
                        messages=(),
                        index_snapshot="2026-09-28",
                    ),
                    SentenceEvent(
                        sentence=SentenceOut(
                            index=0,
                            text="Workers receive annual leave.",
                            verdict="supported",
                            passage_ids=("P1",),
                            reasons=("GROUNDED",),
                            rollbacks=0,
                        ),
                        citations=(
                            RenderedCitation(
                                passage_id="P1",
                                coordinate="uk/uksi/1998/1833/reg14",
                                label="reg.14",
                                url="http://example.com",
                            ),
                        ),
                    ),
                    NoticeEvent(code="provider_fallback", message="Backup used."),
                    DoneEvent(timings=Timings(1.0, 2.0, 12.0)),
                ]
                elapsed = 12.0
            lines.append(
                json.dumps(
                    {
                        "id": rid,
                        "events": [to_jsonable(e) for e in events],
                        "elapsed_ms": elapsed,
                    }
                )
            )
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0

    return fake_recorder


def test_legal_answers_writes_both_reports_with_fake_recorder(tmp_path: Path) -> None:
    battery_path, seal_path = _write_sample_battery_and_seal(tmp_path)
    recorded_calls: list[dict[str, Any]] = []
    out_dir = tmp_path / "reports"
    rc, report = run_legal_answers(
        split="dev",
        battery_path=battery_path,
        seal_path=seal_path,
        pace=15.0,
        label="abc1234",
        out_dir=out_dir,
        recorder=_make_fake_recorder(recorded_calls),
        utc_date="2026-11-25",
    )
    assert rc == 1  # L41 failed
    assert report is not None
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["ids"] == ["L01", "L41"]  # L03 (answer_checks=null) excluded
    assert recorded_calls[0]["pace"] == 15.0
    assert recorded_calls[0]["target"] == "local"

    json_file = out_dir / "2026-11-25-abc1234-dev.json"
    md_file = out_dir / "2026-11-25-abc1234-dev.md"
    assert json_file.is_file()
    assert md_file.is_file()

    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["totals"]["rows_total"] == 2
    assert data["totals"]["rows_passed"] == 1
    assert data["totals"]["rows_failed"] == 1
    assert data["totals"]["incomplete_notices_by_reason"] == {"providers_unavailable": 1}

    by_id = {r["id"]: r for r in data["rows"]}
    assert by_id["L01"]["passed"] is True
    assert by_id["L01"]["failed_checks"] == []
    assert by_id["L01"]["elapsed_ms"] == 33.6
    assert by_id["L01"]["providers_used"] == ["groq", "cloudflare"]
    assert by_id["L01"]["incomplete_notice"] is True
    assert by_id["L01"]["incomplete_reason"] == "providers_unavailable"
    assert len(by_id["L01"]["answer_text"]) == ANSWER_TEXT_LIMIT

    assert by_id["L41"]["passed"] is False
    assert any("missing required citations" in c for c in by_id["L41"]["failed_checks"])
    assert by_id["L41"]["incomplete_notice"] is False
    assert by_id["L41"]["incomplete_reason"] is None

    md_text = md_file.read_text(encoding="utf-8")
    assert "Rows passed: 1/2" in md_text
    assert "providers_unavailable: 1" in md_text
    for bad in ("\u2014", "\u2013", "\u2192", "\u201c", "\u201d", "\u2265", "\u2248", "\u00d7"):
        assert bad not in md_text
