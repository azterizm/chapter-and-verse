"""Postgres persistence (agent C1): SQL builders, the shared quota ledger, receipts.

Unit tests use a fake connection. The integration test runs against a real Postgres only when
``TEST_DATABASE_URL`` is set (the schema is applied first); otherwise it is skipped.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from chapterverse import persistence
from chapterverse.config import Settings
from chapterverse.contracts import AnswerFactObject, canonical_json, sha256_hex
from chapterverse.persistence import (
    DELETE_OLD_DAILY_BUDGET_SQL,
    DELETE_OLD_DAILY_CAPS_SQL,
    INSERT_RECEIPT_SQL,
    PURGE_SQL,
    READ_DAILY_BUDGET_SQL,
    READ_DAILY_CAP_SQL,
    READ_TOTALS_SQL,
    UPSERT_DAILY_BUDGET_SQL,
    UPSERT_DAILY_CAP_SQL,
    UPSERT_QUOTA_SQL,
    Database,
    PostgresDailyBudget,
    PostgresDailyCap,
    PostgresLedger,
    QuotaDelta,
    ReceiptStore,
    daily_budget_cleanup_params,
    daily_budget_read_params,
    daily_budget_upsert_params,
    daily_cap_read_params,
    daily_cap_upsert_params,
    daily_caps_cleanup_params,
    daily_cleanup_sql,
    open_persistence,
    quota_delta,
    quota_params,
    receipt_params,
    totals_params,
    window_start,
)
from chapterverse.providers.ledger import (
    CallUsage,
    GroqLimits,
    InMemoryLedger,
    QuotaLedger,
    RateHeaders,
)
from chapterverse.ratelimit import DailyBudgetCounts, client_key_hash

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 19, 13, 47, 31, 5, tzinfo=UTC)


class FakeConnection:
    def __init__(self, log: list[tuple[str, Any]], rows: list[tuple[Any, ...]] | None = None):
        self.log = log
        self.rows = rows or []
        self.closed = False

    def execute(self, query: Any, params: Any = ()) -> FakeConnection:
        self.log.append((str(query), params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)

    def close(self) -> None:
        self.closed = True


def fake_db(
    log: list[tuple[str, Any]], rows: list[tuple[Any, ...]] | None = None, **kwargs: Any
) -> Database:
    return Database("postgres://u:pw@h/db", connect=lambda _: FakeConnection(log, rows),
                    background=False, **kwargs)  # fmt: skip


def fact() -> AnswerFactObject:
    return AnswerFactObject(
        query_sha256=sha256_hex("q"),
        route_status="ROUTE_BOUNDED",
        coordinates=("uk/ukpga/1996/18/s124",),
        passages={"P1": "uk/ukpga/1996/18/s124/1"},
        version_dates={"uk/ukpga/1996/18": "2026-07-29"},
        spans={},
        sentences=(),
        corpus_id="c",
        calls=(),
        policy_hash="p",
        mode="extractive",
    )


# ---------------------------------------------------------------------- builders


def test_window_starts_are_utc_truncations() -> None:
    assert window_start("minute", NOW) == datetime(2026, 11, 19, 13, 47, tzinfo=UTC)
    assert window_start("day", NOW) == datetime(2026, 11, 19, tzinfo=UTC)
    assert window_start("month", NOW) == datetime(2026, 11, 1, tzinfo=UTC)
    assert totals_params(NOW) == (window_start("day", NOW), window_start("month", NOW))


def test_quota_params_one_row_per_window() -> None:
    delta = QuotaDelta(requests=1, input_tokens=900, output_tokens=60, neurons=1.23456)
    rows = quota_params("cloudflare", delta, NOW)
    assert [r[1] for r in rows] == ["minute", "day", "month"]
    assert rows[0] == ("cloudflare", "minute", window_start("minute", NOW), 1, 900, 60, 1.235,
                       0.0, 0, 0)  # fmt: skip
    assert UPSERT_QUOTA_SQL.count("%s") == len(rows[0])
    assert "ON CONFLICT (provider, window_kind, window_start)" in UPSERT_QUOTA_SQL
    assert READ_TOTALS_SQL.count("%s") == 2


def test_quota_delta_from_usage_and_ledger_snapshots() -> None:
    ledger = InMemoryLedger(clock=NOW.timestamp)
    before = ledger.snapshot()
    usage = CallUsage("cloudflare", est_tokens=100, max_output_tokens=50, prompt_tokens=80,
                      completion_tokens=20, neurons=9.5)  # fmt: skip
    ledger.record(usage)
    delta = quota_delta(usage, before, ledger.snapshot(), modal_usd_per_s=0.0006)
    assert delta == QuotaDelta(requests=1, input_tokens=80, output_tokens=20, neurons=9.5)
    unsent = CallUsage("groq", est_tokens=100, sent=False)
    assert quota_delta(unsent, before, before, modal_usd_per_s=1.0).empty
    limited = CallUsage("groq", est_tokens=100, headers=RateHeaders(retry_after_s=3.0))
    assert quota_delta(limited, before, before, modal_usd_per_s=1.0).rate_limited == 1
    estimate = CallUsage("groq", est_tokens=123)
    assert quota_delta(estimate, before, before, modal_usd_per_s=1.0).input_tokens == 123


def test_quota_delta_modal_gpu_seconds() -> None:
    ledger = InMemoryLedger(clock=NOW.timestamp)
    before = ledger.snapshot()
    usage = CallUsage("modal", est_tokens=0, latency_s=2.0)
    ledger.record(usage)
    rate = ledger.modal_limits.gpu_usd_per_s * ledger.modal_limits.overhead
    delta = quota_delta(usage, before, ledger.snapshot(), modal_usd_per_s=rate)
    # A first call pays the measured cold start and the scaledown tail.
    expected = ledger.modal_limits.cold_start_s + ledger.modal_limits.scaledown_s
    assert delta.gpu_seconds == pytest.approx(expected)


def test_receipt_params_hold_the_hash_never_the_query() -> None:
    answer_id, body = receipt_params(fact())
    assert answer_id == fact().answer_id
    assert body == canonical_json(fact()).decode()
    assert sha256_hex(body) == answer_id
    assert '"query"' not in body
    assert INSERT_RECEIPT_SQL.count("%s") == 2


# ---------------------------------------------------------------------- ledger


def test_postgres_ledger_is_a_quota_ledger_and_writes_each_call() -> None:
    log: list[tuple[str, Any]] = []
    ledger = PostgresLedger(fake_db(log), wall_clock=NOW.timestamp)
    assert isinstance(ledger, QuotaLedger)
    assert ledger.reserve("groq", 100, max_output_tokens=200)
    ledger.record(CallUsage("groq", 100, max_output_tokens=200, prompt_tokens=90,
                            completion_tokens=10))  # fmt: skip
    upserts = [p for sql, p in log if sql == UPSERT_QUOTA_SQL]
    assert [p[1] for p in upserts] == ["minute", "day", "month"]
    assert upserts[0][3:6] == (1, 90, 10)
    assert ledger.totals("groq", "day").requests == 1
    assert ledger.inner.snapshot()["groq"]["pending_requests"] == 0


def test_shared_day_totals_refuse_and_release_the_local_hold() -> None:
    rows = [
        ("groq", "day", 1000, 5000, 1000, 0, 0),
        ("cloudflare", "day", 10, 0, 0, 9499.9, 0),
        ("modal", "month", 3, 0, 0, 0, 1_000_000),
    ]
    log: list[tuple[str, Any]] = []
    ledger = PostgresLedger(fake_db(log, rows), wall_clock=NOW.timestamp)
    assert not ledger.reserve("groq", 100)
    assert ledger.inner.snapshot()["groq"]["pending_requests"] == 0
    assert not ledger.available("groq")
    assert not ledger.reserve("cloudflare", 100, max_output_tokens=100)
    assert not ledger.available("modal")
    assert ledger.reserve("other", 1)
    reads = [sql for sql, _ in log if sql == READ_TOTALS_SQL]
    assert len(reads) == 1  # cached for refresh_s


def test_database_down_fails_open_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    tries: list[int] = []

    def connect(dsn: str) -> FakeConnection:
        tries.append(1)
        raise OSError(f"could not connect to {dsn}")

    now = [0.0]
    db = Database("postgres://u:hunter2@h/db", connect=connect, background=False,
                  clock=lambda: now[0])  # fmt: skip
    ledger = PostgresLedger(db, refresh_s=0.0, wall_clock=NOW.timestamp)
    for _ in range(5):
        assert ledger.reserve("groq", 10)
        ledger.record(CallUsage("groq", 10, prompt_tokens=5, completion_tokens=5))
    assert len(tries) == 1  # waits retry_s before trying again
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1
    assert "OSError" in warnings[0]
    assert "hunter2" not in warnings[0]
    assert "hunter2" not in repr(db)
    now[0] = 61.0
    ledger.reserve("groq", 10)
    assert len(tries) == 2


def test_database_background_writes_flush_on_close() -> None:
    log: list[tuple[str, Any]] = []
    db = Database("postgres://x", connect=lambda _: FakeConnection(log), background=True)
    db.submit("SELECT %s", [(1,), (2,)])
    db.close()
    assert log == [("SELECT %s", (1,)), ("SELECT %s", (2,))]


def test_receipt_store_saves_and_purges() -> None:
    log: list[tuple[str, Any]] = []
    store = ReceiptStore(fake_db(log))
    store.save(fact())
    assert log[0] == (INSERT_RECEIPT_SQL, receipt_params(fact()))
    assert store.purge()
    assert [sql for sql, _ in log[1:]] == list(PURGE_SQL)


def test_open_persistence_without_a_database(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO)
    persistence._LOGGED_NO_DATABASE.clear()
    s = Settings(legal_data_root=tmp_path, router_index=tmp_path, concepts_index=tmp_path)
    first = open_persistence(s)
    second = open_persistence(s)
    assert isinstance(first.ledger, InMemoryLedger)
    assert first.receipts is None
    assert second.database is None
    logged = [r for r in caplog.records if "DATABASE_URL is not set" in r.getMessage()]
    assert len(logged) == 1
    first.close()


def test_open_persistence_with_a_database_is_lazy(tmp_path: Path) -> None:
    calls: list[str] = []

    def connect(dsn: str) -> FakeConnection:
        calls.append(dsn)
        return FakeConnection([])

    s = Settings(
        legal_data_root=tmp_path,
        router_index=tmp_path,
        concepts_index=tmp_path,
        database_url="postgres://u:p@h/db",
    )
    opened = open_persistence(s, connect=connect, background=False)
    assert isinstance(opened.ledger, PostgresLedger)
    assert isinstance(opened.receipts, ReceiptStore)
    assert calls == []
    opened.close()


def test_open_persistence_propagates_custom_groq_limits(tmp_path: Path) -> None:
    s = Settings(
        legal_data_root=tmp_path,
        router_index=tmp_path,
        concepts_index=tmp_path,
        groq_tokens_per_minute=25_000,
        groq_tokens_per_day=500_000,
        groq_requests_per_minute=60,
        groq_requests_per_day=2_500,
    )
    mem = open_persistence(s)
    assert isinstance(mem.ledger, InMemoryLedger)
    assert mem.ledger.groq_limits == GroqLimits(
        requests_per_day=2_500,
        tokens_per_minute=25_000,
        requests_per_minute=60,
        tokens_per_day=500_000,
    )
    mem.close()

    rows = [("groq", "day", 1000, 200_000, 0, 0, 0)]
    log: list[tuple[str, Any]] = []
    db_settings = Settings(
        legal_data_root=tmp_path,
        router_index=tmp_path,
        concepts_index=tmp_path,
        database_url="postgres://u:p@h/db",
        groq_tokens_per_minute=25_000,
        groq_tokens_per_day=500_000,
        groq_requests_per_minute=60,
        groq_requests_per_day=2_500,
    )
    opened = open_persistence(
        db_settings,
        connect=lambda _: FakeConnection(log, rows),
        background=False,
    )
    assert isinstance(opened.ledger, PostgresLedger)
    # 1000 requests / 200K tokens would be refused under free defaults, allowed under custom limits.
    assert opened.ledger.reserve("groq", 1_000)
    opened.ledger.record(CallUsage("groq", 1_000, sent=False))
    # Explicit groq_limits override takes precedence over settings fields.
    capped = open_persistence(
        db_settings,
        connect=lambda _: FakeConnection(log, rows),
        background=False,
        groq_limits=GroqLimits(requests_per_day=1000),
    )
    assert isinstance(capped.ledger, PostgresLedger)
    assert not capped.ledger.reserve("groq", 100)
    opened.close()
    capped.close()


def test_postgres_ledger_window_wait_unit() -> None:
    # 1. When shared day totals in database refuse, returns ("day", wait_s)
    rows = [("groq", "day", 1000, 5000, 1000, 0, 0)]
    log: list[tuple[str, Any]] = []
    ledger = PostgresLedger(fake_db(log, rows), wall_clock=NOW.timestamp)
    wait = ledger.window_wait("groq", 100)
    assert wait is not None
    assert wait[0] == "day"
    assert wait[1] > 0

    # 2. When shared limits pass, but inner per-minute window refuses:
    empty_log: list[tuple[str, Any]] = []
    clock_now = [1000.0]
    inner = InMemoryLedger(clock=lambda: clock_now[0])
    inner.record(
        CallUsage(
            "groq",
            0,
            headers=RateHeaders(limit_tokens=8000, remaining_tokens=100, reset_tokens_s=3.0),
        )
    )
    ledger2 = PostgresLedger(fake_db(empty_log, []), inner=inner, wall_clock=NOW.timestamp)
    wait2 = ledger2.window_wait("groq", 500)
    assert wait2 is not None
    assert wait2[0] == "minute"
    assert wait2[1] == pytest.approx(3.0)

    # 3. When everything fits:
    assert ledger2.window_wait("groq", 50) is None


# ---------------------------------------------------------------------- daily caps


def test_daily_cap_sql_builders_and_schema() -> None:
    today = NOW.date()
    ip = "192.0.2.1"
    key = client_key_hash(ip, "salt-123")

    # Key hashing never stores the raw IP
    assert ip not in key
    assert len(key) == 64

    # Read builder
    read_params = daily_cap_read_params(key, today)
    assert read_params == (key, today)
    assert READ_DAILY_CAP_SQL.count("%s") == len(read_params)
    assert "daily_caps" in READ_DAILY_CAP_SQL

    # Upsert builder
    upsert_params = daily_cap_upsert_params(key, today, 1)
    assert upsert_params == (key, today, 1)
    assert UPSERT_DAILY_CAP_SQL.count("%s") == len(upsert_params)
    assert "ON CONFLICT (key_hash, day) DO UPDATE" in UPSERT_DAILY_CAP_SQL

    # Retention statement in PURGE_SQL
    assert any("daily_caps" in stmt for stmt in PURGE_SQL)


def test_postgres_daily_cap_unit_without_database() -> None:
    today = NOW.date()
    key = client_key_hash("192.0.2.2", "salt")
    log: list[tuple[str, Any]] = []

    # 1. DB has existing count: get reads from DB
    cap = PostgresDailyCap(fake_db(log, [(5,)]))
    assert cap.get(key, today) == 5
    assert log[0] == (READ_DAILY_CAP_SQL, (key, today))

    # 2. DB has no row (empty): returns 0
    empty_log: list[tuple[str, Any]] = []
    cap_empty = PostgresDailyCap(fake_db(empty_log, []))
    assert cap_empty.get(key, today) == 0

    # 3. Increment submits UPSERT_DAILY_CAP_SQL and updates locally
    write_log: list[tuple[str, Any]] = []
    db = fake_db(write_log, [])
    cap_write = PostgresDailyCap(db)
    assert cap_write.increment(key, today) == 1
    assert cap_write.increment(key, today) == 2
    assert write_log == [
        (UPSERT_DAILY_CAP_SQL, (key, today, 1)),
        (UPSERT_DAILY_CAP_SQL, (key, today, 1)),
    ]

    # 4. Fallback to inner when DB is down
    def fail_connect(_: str) -> FakeConnection:
        raise RuntimeError("down")

    down_db = Database("postgres://u:pw@h/db", connect=fail_connect, background=False)
    down_cap = PostgresDailyCap(down_db)
    assert down_cap.get(key, today) == 0
    assert down_cap.increment(key, today) == 1
    assert down_cap.get(key, today) == 1
    assert "PostgresDailyCap" in repr(down_cap)


def test_daily_budget_sql_builders_and_schema() -> None:
    today = NOW.date()

    # Read builder
    read_params = daily_budget_read_params(today)
    assert read_params == (today,)
    assert READ_DAILY_BUDGET_SQL.count("%s") == len(read_params)
    assert "daily_budget" in READ_DAILY_BUDGET_SQL

    # Upsert builder
    upsert_params_other = daily_budget_upsert_params(today, hosting=False)
    assert upsert_params_other == (today, 1, 0)
    assert UPSERT_DAILY_BUDGET_SQL.count("%s") == len(upsert_params_other)
    assert "ON CONFLICT (day) DO UPDATE" in UPSERT_DAILY_BUDGET_SQL

    upsert_params_hosting = daily_budget_upsert_params(today, hosting=True)
    assert upsert_params_hosting == (today, 1, 1)

    # Cleanup builders
    cleanup_params = daily_budget_cleanup_params(today, 2)
    assert cleanup_params == (today - timedelta(days=2),)
    assert DELETE_OLD_DAILY_BUDGET_SQL.count("%s") == len(cleanup_params)

    caps_cleanup = daily_caps_cleanup_params(today, 2)
    assert caps_cleanup == (today - timedelta(days=2),)
    assert DELETE_OLD_DAILY_CAPS_SQL.count("%s") == len(caps_cleanup)

    # daily_cleanup_sql statement builder
    assert daily_cleanup_sql("daily_budget") == (
        "DELETE FROM daily_budget WHERE day < (now() AT TIME ZONE 'UTC')::date - 2"
    )
    assert daily_cleanup_sql("daily_caps") == (
        "DELETE FROM daily_caps WHERE day < (now() AT TIME ZONE 'UTC')::date - 2"
    )
    with pytest.raises(ValueError, match="unknown table"):
        daily_cleanup_sql("other_table")

    # Retention statement in PURGE_SQL
    assert any("daily_budget" in stmt for stmt in PURGE_SQL)
    assert any("daily_caps" in stmt for stmt in PURGE_SQL)


def test_postgres_daily_budget_unit_without_database() -> None:
    today = NOW.date()
    log: list[tuple[str, Any]] = []

    # 1. DB has existing count: get reads from DB
    budget = PostgresDailyBudget(fake_db(log, [(10, 3)]))
    assert budget.get(today) == DailyBudgetCounts(10, 3)
    assert log[0] == (READ_DAILY_BUDGET_SQL, (today,))

    # 2. DB has no row (empty): returns 0, 0
    empty_log: list[tuple[str, Any]] = []
    budget_empty = PostgresDailyBudget(fake_db(empty_log, []))
    assert budget_empty.get(today) == DailyBudgetCounts(0, 0)

    # 3. Increment submits UPSERT_DAILY_BUDGET_SQL and cleanup queries
    write_log: list[tuple[str, Any]] = []
    db = fake_db(write_log, [])
    budget_write = PostgresDailyBudget(db)
    assert budget_write.increment(today, hosting=False) == DailyBudgetCounts(1, 0)
    assert budget_write.increment(today, hosting=True) == DailyBudgetCounts(2, 1)
    cutoff = today - timedelta(days=2)
    assert write_log == [
        (UPSERT_DAILY_BUDGET_SQL, (today, 1, 0)),
        (DELETE_OLD_DAILY_BUDGET_SQL, (cutoff,)),
        (DELETE_OLD_DAILY_CAPS_SQL, (cutoff,)),
        (UPSERT_DAILY_BUDGET_SQL, (today, 1, 1)),
    ]

    # 4. Fallback to inner when DB is down
    def fail_connect(_: str) -> FakeConnection:
        raise RuntimeError("down")

    down_db = Database("postgres://u:pw@h/db", connect=fail_connect, background=False)
    down_budget = PostgresDailyBudget(down_db)
    assert down_budget.get(today) == DailyBudgetCounts(0, 0)
    assert down_budget.increment(today, hosting=True) == DailyBudgetCounts(1, 1)
    assert down_budget.get(today) == DailyBudgetCounts(1, 1)
    assert "PostgresDailyBudget" in repr(down_budget)


# ---------------------------------------------------------------------- real Postgres


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run against a real Postgres")
    return url


def test_against_a_real_postgres(database_url: str) -> None:
    import psycopg  # noqa: PLC0415

    schema = (REPO / "deploy" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(schema.encode())
    provider = f"cvtest-{secrets.token_hex(4)}"
    db = Database(database_url, background=False)
    try:
        ledger = PostgresLedger(db, refresh_s=0.0)
        ledger.record(CallUsage(provider, 10, prompt_tokens=7, completion_tokens=3))
        ledger.record(CallUsage(provider, 10, prompt_tokens=7, completion_tokens=3))
        fresh = PostgresLedger(db, refresh_s=0.0)
        day = fresh.totals(provider, "day")
        assert (day.requests, day.tokens) == (2, 20)
        receipts = ReceiptStore(db)
        receipts.save(fact())
        receipts.save(fact())  # the same answer is stored once
        rows = db.fetchall("SELECT fact FROM receipts WHERE answer_id = %s", (fact().answer_id,))
        assert rows is not None
        assert len(rows) == 1
        assert rows[0][0]["query_sha256"] == sha256_hex("q")
        assert (
            db.execute_many(
                "INSERT INTO receipts (answer_id, fact) VALUES (%s, %s::jsonb)",
                [("f" * 64, '{"query": "raw text"}')],
            )
            is False
        )  # the schema's CHECK refuses a query field
        assert receipts.purge()
        daily = PostgresDailyCap(db)
        today = datetime.now(UTC).date()
        daily_key = client_key_hash("198.51.100.99", "salt")
        assert daily.get(daily_key, today) == 0
        assert daily.increment(daily_key, today) == 1
        assert daily.increment(daily_key, today) == 2
        fresh_daily = PostgresDailyCap(db)
        assert fresh_daily.get(daily_key, today) == 2
    finally:
        db.execute_many("DELETE FROM provider_quota WHERE provider = %s", [(provider,)])
        db.execute_many("DELETE FROM receipts WHERE answer_id = %s", [(fact().answer_id,)])
        db.execute_many("DELETE FROM daily_caps WHERE key_hash = %s", [(daily_key,)])
        db.close()
