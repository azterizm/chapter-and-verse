"""Tests for scripts/receipt.py (requirements R1-R6)."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from chapterverse.contracts import AnswerFactObject, ProviderCall, SentenceOut  # noqa: E402
from scripts.receipt import (  # noqa: E402
    SELECT_QUOTA_DAY_SQL,
    SELECT_QUOTA_MINUTE_SQL,
    SELECT_RECEIPT_SQL,
    connect_db,
    get_dsn,
    main,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, Sequence[Any]]] = []

    def execute(self, sql: str, params: Sequence[Any]) -> FakeCursor:
        self.executed.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor_obj = FakeCursor(rows)
        self.closed = False

    def execute(self, sql: str, params: Sequence[Any]) -> FakeCursor:
        self.cursor_obj.execute(sql, params)
        return self.cursor_obj

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


def _sample_fact() -> AnswerFactObject:
    return AnswerFactObject(
        query_sha256="0" * 64,
        route_status="ROUTE_BOUNDED",
        coordinates=("uk/ukpga/1996/18/s124",),
        passages={"P1": "uk/ukpga/1996/18/s124/1ZA"},
        version_dates={"uk/ukpga/1996/18": "2026-07-29"},
        spans={"uk/ukpga/1996/18/s124/1ZA": (54, 76)},
        sentences=(
            SentenceOut(
                index=0,
                text="The limit is £123,543.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=1,
            ),
        ),
        corpus_id="fixture",
        calls=(
            ProviderCall(
                provider="groq",
                model_id="llama-3.3-70b-versatile",
                quantisation=None,
                purpose="generate",
                prompt_tokens=120,
                completion_tokens=45,
                latency_ms=250.5,
            ),
            ProviderCall(
                provider="cloudflare",
                model_id="deepseek-r1-distill-qwen-32b",
                quantisation="int8",
                purpose="judge",
                prompt_tokens=80,
                completion_tokens=20,
                latency_ms=110.0,
            ),
        ),
        policy_hash="a" * 64,
        mode="generated",
    )


# ----------------------------------------------------------------------------- Argument validation


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["1234567"],  # 7 chars (too short)
        ["12345678901234567890123456789012345678901234567890123456789012345"],  # 65 chars
        ["E18112AE91E4"],  # uppercase
        ["g18112ae91e4"],  # non-hex
        ["--quota", "2026-02-31"],  # invalid date
        ["--quota", "bad-date"],
        ["--minutes", "14:00-14:30"],  # --minutes without --quota
        ["--quota", "2026-11-24", "--minutes", "bad"],
        ["--quota", "2026-11-24", "--minutes", "14:30-14:00"],  # start > end
        ["--quota", "2026-11-24", "--minutes", "25:00-26:00"],
        ["--quota", "2026-11-24", "e18112ae91e4"],  # prefix with --quota
        ["--file", "fact.json", "e18112ae91e4"],  # prefix with --file
        ["--file", "fact.json", "--quota", "2026-11-24"],  # file with quota
        ["--unknown-flag"],
    ],
)
def test_argument_validation_exits_2(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err


# ---------------------------------------------------------------------- Prefix to LIKE parameter


def test_prefix_to_like_parameter(capsys: pytest.CaptureFixture[str]) -> None:
    fact = _sample_fact()
    created_at = datetime(2026, 11, 24, 18, 51, 26, tzinfo=UTC)
    fake_conn = FakeConnection(rows=[(fact.answer_id, fact.to_dict(), created_at)])

    record_calls: list[dict[str, Any]] = []

    def fake_connect(dsn: str, **kwargs: Any) -> FakeConnection:
        record_calls.append({"dsn": dsn, "kwargs": kwargs})
        return fake_conn

    ret = main(["e18112ae91e4"], connect_fn=fake_connect, dsn="postgres://fake")
    assert ret == 0

    assert len(fake_conn.cursor_obj.executed) == 1
    sql, params = fake_conn.cursor_obj.executed[0]
    assert sql == SELECT_RECEIPT_SQL
    assert params == ("e18112ae91e4%",)


# ----------------------------------------------------------------- Read-only connection options


def test_read_only_connection_options_passed() -> None:
    recorded_kwargs: list[dict[str, Any]] = []

    def fake_connect(dsn: str, **kwargs: Any) -> FakeConnection:
        recorded_kwargs.append(kwargs)
        return FakeConnection()

    conn = connect_db("postgres://fake", connect_fn=fake_connect)
    assert isinstance(conn, FakeConnection)
    assert len(recorded_kwargs) == 1
    assert recorded_kwargs[0] == {"options": "-c default_transaction_read_only=on"}


# ----------------------------------------------------------- Output on fixture fact & round trip


def test_output_on_fixture_fact_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fact = _sample_fact()
    data = fact.to_dict()
    round_trip = AnswerFactObject.from_dict(data)
    assert round_trip == fact

    fact_file = tmp_path / "fact.json"
    fact_file.write_text(json.dumps(data), encoding="utf-8")

    # Plain text output
    ret = main(["--file", str(fact_file)])
    assert ret == 0
    captured = capsys.readouterr()
    out = captured.out

    # Check top metadata
    assert f"answer id:         {fact.answer_id}" in out
    assert "mode:              generated" in out
    assert "route status:      ROUTE_BOUNDED" in out
    assert "bound coordinates: uk/ukpga/1996/18/s124" in out
    assert f"policy hash:       {fact.policy_hash}" in out
    assert "corpus id:         fixture" in out

    # Check passages
    assert "P1" in out
    assert "uk/ukpga/1996/18/s124/1ZA" in out
    assert "54-76" in out
    assert "2026-07-29" in out

    # Check sentences
    assert "[0] verdict: supported | rollbacks: 1" in out
    assert "passages: P1 | reasons: GROUNDED" in out
    assert "text: The limit is £123,543." in out

    # Check provider calls
    assert "groq | purpose: generate | model: llama-3.3-70b-versatile" in out
    assert "latency: 250.5 ms | tokens: 120 prompt / 45 completion" in out
    assert "cloudflare | purpose: judge | model: deepseek-r1-distill-qwen-32b (int8)" in out
    assert "latency: 110.0 ms | tokens: 80 prompt / 20 completion" in out

    # Check totals
    assert "calls per provider and purpose:" in out
    assert "groq / generate: 1" in out
    assert "cloudflare / judge: 1" in out
    assert "summed latency per purpose:" in out
    assert "generate: 250.5 ms" in out
    assert "judge: 110.0 ms" in out
    assert "rollbacks: 1" in out

    # Check --json output
    ret_json = main(["--file", str(fact_file), "--json"])
    assert ret_json == 0
    captured_json = capsys.readouterr()
    json_out = json.loads(captured_json.out)
    assert AnswerFactObject.from_dict(json_out) == fact


# ------------------------------------------------------------- DSN never appears in stdout/stderr


def test_dsn_never_appears_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    secret_token = "secret_canary_password_xyz_9988"
    fake_dsn = f"postgresql://app_user:{secret_token}@db.cloud.test:5432/chapterverse"

    fact = _sample_fact()
    created_at = datetime(2026, 11, 24, 18, 51, 26, tzinfo=UTC)
    fake_conn = FakeConnection(rows=[(fact.answer_id, fact.to_dict(), created_at)])

    ret = main(
        ["e18112ae91e4"],
        connect_fn=lambda _d, **_kw: fake_conn,
        dsn=fake_dsn,
    )
    assert ret == 0
    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err


def test_dsn_never_appears_on_connect_error(capsys: pytest.CaptureFixture[str]) -> None:
    secret_token = "secret_canary_password_xyz_9988"
    fake_dsn = f"postgresql://app_user:{secret_token}@db.cloud.test:5432/chapterverse"

    def failing_connect(_d: str, **_kw: Any) -> Any:
        raise ConnectionRefusedError(f"could not connect to {fake_dsn}")

    with pytest.raises(SystemExit) as exc:
        main(
            ["e18112ae91e4"],
            connect_fn=failing_connect,
            dsn=fake_dsn,
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err
    assert "ConnectionRefusedError" in captured.err


# ------------------------------------------------------ --quota SQL is a SELECT with parameters


def test_quota_sql_select_with_parameters(capsys: pytest.CaptureFixture[str]) -> None:
    day_rows: list[tuple[Any, ...]] = [
        ("groq", 8, 2400, 600, 1, 0),
        ("cloudflare", 5, 1500, 300, 0, 0),
    ]
    minute_rows: list[tuple[Any, ...]] = [
        ("groq", datetime(2026, 11, 24, 14, 10, tzinfo=UTC), 1, 300, 80, 0, 0),
    ]

    class QuotaFakeConnection:
        def __init__(self) -> None:
            self.executed: list[tuple[str, Sequence[Any]]] = []
            self.closed = False

        def execute(self, sql: str, params: Sequence[Any]) -> FakeCursor:
            self.executed.append((sql, params))
            if "minute" in sql:
                return FakeCursor(minute_rows)
            return FakeCursor(day_rows)

        def close(self) -> None:
            self.closed = True

    fake_conn = QuotaFakeConnection()

    ret = main(
        ["--quota", "2026-11-24", "--minutes", "14:00-14:30"],
        connect_fn=lambda _d, **_kw: fake_conn,
        dsn="postgres://fake",
    )
    assert ret == 0
    assert len(fake_conn.executed) == 2

    # Day query check
    day_sql, day_params = fake_conn.executed[0]
    assert day_sql == SELECT_QUOTA_DAY_SQL
    assert day_sql.strip().startswith("SELECT")
    assert day_params == (datetime(2026, 11, 24, 0, 0, tzinfo=UTC),)

    # Minute query check
    min_sql, min_params = fake_conn.executed[1]
    assert min_sql == SELECT_QUOTA_MINUTE_SQL
    assert min_sql.strip().startswith("SELECT")
    assert min_params == (
        datetime(2026, 11, 24, 14, 0, tzinfo=UTC),
        datetime(2026, 11, 24, 14, 30, tzinfo=UTC),
    )

    captured = capsys.readouterr()
    out = captured.out
    assert "provider quota for 2026-11-24 (UTC day):" in out
    assert "groq" in out
    assert "2400 / 600" in out
    assert "minute rows (14:00 to 14:30):" in out
    assert "2026-11-24 14:10:00 UTC" in out


# ------------------------------------------------------------- Zero and multiple matches


def test_zero_matches_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    fake_conn = FakeConnection(rows=[])
    with pytest.raises(SystemExit) as exc:
        main(
            ["e18112ae91e4"],
            connect_fn=lambda _d, **_kw: fake_conn,
            dsn="postgres://fake",
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "no receipt" in captured.err


def test_multiple_matches_exits_1_and_lists(capsys: pytest.CaptureFixture[str]) -> None:
    id1 = "e18112ae91e4" + "0" * 52
    id2 = "e18112ae91e4" + "1" * 52
    t1 = datetime(2026, 11, 24, 18, 50, tzinfo=UTC)
    t2 = datetime(2026, 11, 24, 18, 40, tzinfo=UTC)
    fake_conn = FakeConnection(rows=[(id1, {}, t1), (id2, {}, t2)])

    with pytest.raises(SystemExit) as exc:
        main(
            ["e18112ae91e4"],
            connect_fn=lambda _d, **_kw: fake_conn,
            dsn="postgres://fake",
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert id1 in captured.err
    assert id2 in captured.err


# ----------------------------------------------------------------- Offline file errors


def test_file_not_found_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nonexistent.json"
    with pytest.raises(SystemExit) as exc:
        main(["--file", str(missing)])
    assert exc.value.code == 1
    assert "file not found" in capsys.readouterr().err


def test_invalid_json_file_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--file", str(bad_file)])
    assert exc.value.code == 1
    assert "invalid JSON file" in capsys.readouterr().err


# ------------------------------------------------------------- DSN resolution from config file


def test_get_dsn_from_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "database_url"
    cfg.write_text("postgres://user:pass@host/db\n", encoding="utf-8")
    dsn = get_dsn(environ={}, config_path=cfg)
    assert dsn == "postgres://user:pass@host/db"
