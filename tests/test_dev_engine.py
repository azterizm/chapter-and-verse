"""Tests for web/mock/record_engine.py, scripts/dev_engine.py, and eval/web_smoke.py."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    AnswerFactObject,
    ClarifyEvent,
    DoneEvent,
    LabelledCoordinate,
    ReceiptEvent,
    RefusalEvent,
    RouteEvent,
    Timings,
    encode_sse,
    sha256_hex,
)
from chapterverse.providers.presets import JUDGE_PRESET_NAME

REPO = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def record_engine_mod() -> ModuleType:
    return _load_module("test_record_engine_mod", REPO / "web" / "mock" / "record_engine.py")


@pytest.fixture(scope="module")
def dev_engine_mod() -> ModuleType:
    return _load_module("test_dev_engine_mod", REPO / "scripts" / "dev_engine.py")


@pytest.fixture(scope="module")
def web_smoke_mod() -> ModuleType:
    return _load_module("test_web_smoke_mod", REPO / "eval" / "web_smoke.py")


# ---------------------------------------------------------------------------- R1 record_engine.py


def test_record_engine_clean_env_strips_db_and_modal(record_engine_mod: ModuleType) -> None:
    raw_env = {
        "PATH": "/usr/bin",
        "DATABASE_URL": "postgres://user:secret@host/db",
        "MODAL_URL": "https://example.modal.run",
        "MODAL_API_KEY": "modal-secret",
        "MODAL_MASK_PROCESSOR": "/var/lib/mask.txt",
        "MODAL_TOKENIZER_JSON": "/var/lib/tok.json",
        "GEN_PROVIDERS": "groq,cloudflare,modal",
        "JUDGE_PROVIDERS": "modal",
        "GROQ_API_KEY": "gsk_test",
    }
    cleaned = record_engine_mod.clean_env(raw_env)
    assert "DATABASE_URL" not in cleaned
    assert not any(k.startswith("MODAL_") for k in cleaned)
    assert cleaned["GEN_PROVIDERS"] == "groq,cloudflare"
    assert cleaned["JUDGE_PROVIDERS"] == "groq,cloudflare"
    assert cleaned["DAILY_ASK_CAP"] == "0"
    assert cleaned["GROQ_API_KEY"] == "gsk_test"


def test_record_engine_records_with_fake_runtime(
    record_engine_mod: ModuleType, tmp_path: Path
) -> None:
    policy = build_policy(JUDGE_PRESET_NAME)
    calls: list[str] = []
    sleeps: list[float] = []
    shutdown_called = False

    class FakePipeline:
        def __init__(self) -> None:
            self.policy = policy

        def run(self, request: Any, emit: Any) -> None:
            calls.append(request.query)
            emit(
                RouteEvent(
                    status="ROUTE_AMBIGUOUS",
                    next_action="ASK_USER",
                    coordinates=(),
                    corrections=(),
                    messages=(),
                    index_snapshot="2026-09-28",
                )
            )
            emit(
                ClarifyEvent(
                    question="Which one?",
                    candidates=(
                        LabelledCoordinate(
                            coordinate="uk/ukpga/1980/58/s2",
                            label="s.2 Limitation Act 1980",
                        ),
                    ),
                )
            )
            emit(
                DoneEvent(
                    timings=Timings(
                        first_source_ms=10.0,
                        first_sentence_ms=None,
                        total_ms=25.0,
                    )
                )
            )

    def fake_shutdown() -> None:
        nonlocal shutdown_called
        shutdown_called = True

    seen_envs: list[dict[str, str]] = []

    def fake_factory(env: dict[str, str]) -> Any:
        seen_envs.append(env)
        return SimpleNamespace(pipeline=FakePipeline(), shutdown=fake_shutdown)

    fixed_now = datetime(2026, 11, 25, 10, 0, 0, tzinfo=UTC)
    index = record_engine_mod.record_all(
        pace=15.0,
        out_dir=tmp_path,
        environ={"DATABASE_URL": "postgres://x", "MODAL_URL": "https://m"},
        runtime_factory=fake_factory,
        sleep=sleeps.append,
        now_utc=lambda: fixed_now,
    )

    assert shutdown_called is True
    assert len(seen_envs) == 1
    assert "DATABASE_URL" not in seen_envs[0]
    assert "MODAL_URL" not in seen_envs[0]

    expected_names = [q.name for q in record_engine_mod.QUESTIONS]
    assert list(index.keys()) == expected_names
    assert len(calls) == len(expected_names)
    assert sleeps == [15.0] * (len(expected_names) - 1)

    written_index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert written_index == index
    for name in expected_names:
        entry = written_index[name]
        assert entry["recorded_at"] == "2026-11-25T10:00:00Z"
        assert entry["policy_hash"] == policy.policy_hash
        sse_path = tmp_path / f"{name}.sse"
        assert sse_path.is_file()
        expected_sse = (
            encode_sse(
                RouteEvent(
                    status="ROUTE_AMBIGUOUS",
                    next_action="ASK_USER",
                    coordinates=(),
                    corrections=(),
                    messages=(),
                    index_snapshot="2026-09-28",
                )
            )
            + encode_sse(
                ClarifyEvent(
                    question="Which one?",
                    candidates=(
                        LabelledCoordinate(
                            coordinate="uk/ukpga/1980/58/s2",
                            label="s.2 Limitation Act 1980",
                        ),
                    ),
                )
            )
            + encode_sse(
                DoneEvent(
                    timings=Timings(first_source_ms=10.0, first_sentence_ms=None, total_ms=25.0)
                )
            )
        )
        assert sse_path.read_bytes() == expected_sse


def test_record_engine_only_updates_single_entry(
    record_engine_mod: ModuleType, tmp_path: Path
) -> None:
    policy = build_policy(JUDGE_PRESET_NAME)
    (tmp_path / "index.json").write_text(
        json.dumps({"tort": {"question": "old", "recorded_at": "old", "policy_hash": "old"}}),
        encoding="utf-8",
    )

    class FakePipeline:
        def __init__(self) -> None:
            self.policy = policy

        def run(self, request: Any, emit: Any) -> None:
            emit(
                RouteEvent(
                    status="ROUTE_OUT_OF_COVERAGE",
                    next_action="DECLARE_OUT_OF_COVERAGE",
                    coordinates=(),
                    corrections=(),
                    messages=(),
                    index_snapshot="2026-09-28",
                )
            )
            emit(
                RefusalEvent(
                    kind="ROUTE_OUT_OF_COVERAGE",
                    message="Outside coverage.",
                    suggestions=(),
                    snapshot="2026-09-28",
                    link=None,
                )
            )
            fact = AnswerFactObject(
                query_sha256=sha256_hex(request.query),
                route_status="ROUTE_OUT_OF_COVERAGE",
                coordinates=(),
                passages={},
                version_dates={},
                spans={},
                sentences=(),
                corpus_id="fake",
                calls=(),
                policy_hash=policy.policy_hash,
                mode="refusal",
            )
            emit(ReceiptEvent(answer_id=fact.answer_id, fact=fact))
            emit(
                DoneEvent(
                    timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=5.0)
                )
            )

    sleeps: list[float] = []
    index = record_engine_mod.record_all(
        only="era-124",
        pace=20.0,
        out_dir=tmp_path,
        environ={},
        runtime_factory=lambda _env: SimpleNamespace(
            pipeline=FakePipeline(), shutdown=lambda: None
        ),
        sleep=sleeps.append,
    )

    assert sleeps == []
    assert "tort" in index
    assert "era-124" in index
    assert (tmp_path / "era-124.sse").is_file()

    with pytest.raises(ValueError, match="unknown scenario"):
        record_engine_mod.record_all(
            only="nonexistent",
            out_dir=tmp_path,
            environ={},
            runtime_factory=lambda _env: SimpleNamespace(
                pipeline=FakePipeline(), shutdown=lambda: None
            ),
        )


# ---------------------------------------------------------------------------- R3 dev_engine.py


def test_dev_engine_refuses_non_loopback_host(dev_engine_mod: ModuleType) -> None:
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        dev_engine_mod.run_preview(host="0.0.0.0")  # noqa: S104
    assert dev_engine_mod.main(["--host", "0.0.0.0"]) == 2  # noqa: S104


def test_dev_engine_strips_db_modal_and_runs_single_worker_uvicorn(
    dev_engine_mod: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded: dict[str, Any] = {}

    def fake_runner(cmd: list[str], *, env: dict[str, str], check: bool) -> Any:
        recorded["cmd"] = cmd
        recorded["env"] = env
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    rc = dev_engine_mod.run_preview(
        host="127.0.0.1",
        port=8123,
        environ={
            "PATH": "/usr/bin",
            "DATABASE_URL": "postgres://user:pass@host/db",
            "MODAL_URL": "https://modal.example",
            "MODAL_API_KEY": "secret",
            "MODAL_MASK_PROCESSOR": "/var/lib/mask.txt",
            "MODAL_TOKENIZER_JSON": "/var/lib/tok.json",
            "REQUIRE_ACCESS_HEADERS": "1",
            "JUDGE_PROVIDERS": "modal",
            "GROQ_API_KEY": "gsk_test",
        },
        runner=fake_runner,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8123" in out

    cmd = recorded["cmd"]
    assert "uvicorn" in cmd
    assert "chapterverse.app:app" in cmd
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "8123"
    assert cmd[cmd.index("--workers") + 1] == "1"

    env = recorded["env"]
    assert "DATABASE_URL" not in env
    assert not any(k.startswith("MODAL_") for k in env)
    assert env["REQUIRE_ACCESS_HEADERS"] == "0"
    assert env["DAILY_ASK_CAP"] == "0"
    assert env["JUDGE_PROVIDERS"] == "groq,cloudflare"


# ---------------------------------------------------------------------------- R4 web_smoke.py


def test_web_smoke_imports_nothing_from_chapterverse(web_smoke_mod: ModuleType) -> None:
    source = (REPO / "eval" / "web_smoke.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("chapterverse")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith("chapterverse")

    assert web_smoke_mod.VIEWPORT_WIDTHS == (1280, 768, 360)
    assert web_smoke_mod.COLOR_SCHEMES == ("light", "dark")
    assert web_smoke_mod.is_csp_violation("Refused to load script")
    assert not web_smoke_mod.is_csp_violation("normal log line")
