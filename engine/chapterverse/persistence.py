"""Postgres access: the shared quota ledger and receipt storage (``deploy/schema.sql``).

:class:`PostgresLedger` implements :class:`~chapterverse.providers.ledger.QuotaLedger`. Every
origin (Oracle now, the Hugging Face standby in Slice 4) shares one ``provider_quota`` table, so
a day's Groq requests or Cloudflare Neurons are counted once whichever origin spent them. The
ledger is two layers:

* an :class:`~chapterverse.providers.ledger.InMemoryLedger` keeps this process's exact state:
  reservations in flight, per-minute windows, Groq's rate headers, Modal's spend model;
* Postgres keeps the shared minute, day and month totals. Before a call, the day and month
  totals (cached for ``refresh_s``) must leave room under the same limits as the in-memory
  ledger: Groq requests and tokens a day, Cloudflare Neurons a day, Modal dollars a month.
  Per-minute limits stay local: a database round trip per call is not worth it for them, and
  Groq's own headers correct the local count after each reply.

After a call, the usage is added to the minute, day and month rows with the upsert the schema
describes. Writes go through one background thread, so a slow database never slows a provider
call. A database failure never fails a request: the in-memory ledger still enforces every
limit, the failure is logged once per outage (exception class only: libpq messages can name the
host and user), and the connection is retried after ``retry_s``.

:class:`ReceiptStore` keeps each Answer Fact Object under its ``answer_id``. The fact holds
``query_sha256``, never the query; the schema's CHECK refuses a ``query`` field as well.

With no ``DATABASE_URL``, :func:`open_persistence` returns an in-memory ledger and no receipt
store, and logs that once.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, Protocol

from chapterverse.config import Settings
from chapterverse.contracts import AnswerFactObject, Purpose, canonical_json
from chapterverse.providers.ledger import (
    JUDGE_OUTPUT_RESERVE,
    CallUsage,
    InMemoryLedger,
    QuotaLedger,
    seconds_to_utc_midnight,
    seconds_to_utc_month,
)
from chapterverse.ratelimit import DailyCapStore, InMemoryDailyCap

__all__ = [
    "INSERT_RECEIPT_SQL",
    "PURGE_SQL",
    "READ_DAILY_CAP_SQL",
    "READ_TOTALS_SQL",
    "UPSERT_DAILY_CAP_SQL",
    "UPSERT_QUOTA_SQL",
    "WINDOW_KINDS",
    "Database",
    "Persistence",
    "PostgresDailyCap",
    "PostgresLedger",
    "QuotaDelta",
    "ReceiptStore",
    "Totals",
    "daily_cap_read_params",
    "daily_cap_upsert_params",
    "open_persistence",
    "quota_delta",
    "quota_params",
    "receipt_params",
    "totals_params",
    "window_start",
]

log = logging.getLogger(__name__)

WindowKind = Literal["minute", "day", "month"]
WINDOW_KINDS: Final[tuple[WindowKind, ...]] = ("minute", "day", "month")

UPSERT_QUOTA_SQL: Final = (
    "INSERT INTO provider_quota (provider, window_kind, window_start, requests, input_tokens, "
    "output_tokens, neurons, gpu_seconds, rate_limited, errors, updated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
    "ON CONFLICT (provider, window_kind, window_start) DO UPDATE SET "
    "requests = provider_quota.requests + EXCLUDED.requests, "
    "input_tokens = provider_quota.input_tokens + EXCLUDED.input_tokens, "
    "output_tokens = provider_quota.output_tokens + EXCLUDED.output_tokens, "
    "neurons = provider_quota.neurons + EXCLUDED.neurons, "
    "gpu_seconds = provider_quota.gpu_seconds + EXCLUDED.gpu_seconds, "
    "rate_limited = provider_quota.rate_limited + EXCLUDED.rate_limited, "
    "errors = provider_quota.errors + EXCLUDED.errors, "
    "updated_at = now()"
)
"""The schema's upsert: add one call's usage to a provider's window row."""

READ_TOTALS_SQL: Final = (
    "SELECT provider, window_kind, requests, input_tokens, output_tokens, neurons, gpu_seconds "
    "FROM provider_quota "
    "WHERE (window_kind = 'day' AND window_start = %s) "
    "OR (window_kind = 'month' AND window_start = %s)"
)
"""Every provider's totals for the current UTC day and month."""

INSERT_RECEIPT_SQL: Final = (
    "INSERT INTO receipts (answer_id, fact) VALUES (%s, %s::jsonb) "
    "ON CONFLICT (answer_id) DO NOTHING"
)
"""One receipt; the same answer (same hash) is stored once."""

READ_DAILY_CAP_SQL: Final = "SELECT count FROM daily_caps WHERE key_hash = %s AND day = %s"
"""Read one client's generated answer count for a UTC day (requirements R3)."""

UPSERT_DAILY_CAP_SQL: Final = (
    "INSERT INTO daily_caps (key_hash, day, count) VALUES (%s, %s, %s) "
    "ON CONFLICT (key_hash, day) DO UPDATE SET count = daily_caps.count + EXCLUDED.count"
)
"""Increment one client's generated answer count for a UTC day (requirements R3)."""

PURGE_SQL: Final[tuple[str, ...]] = (
    "DELETE FROM sessions WHERE expires_at < now()",
    (
        "UPDATE feedback SET note = NULL WHERE note IS NOT NULL "
        "AND created_at < now() - interval '90 days'"
    ),
    "DELETE FROM feedback WHERE created_at < now() - interval '180 days'",
    "DELETE FROM receipts WHERE created_at < now() - interval '180 days'",
    (
        "DELETE FROM provider_quota "
        "WHERE (window_kind = 'minute' AND window_start < now() - interval '2 days') "
        "OR (window_kind = 'day' AND window_start < now() - interval '90 days') "
        "OR (window_kind = 'month' AND window_start < now() - interval '13 months')"
    ),
    "DELETE FROM daily_caps WHERE day < (now() AT TIME ZONE 'UTC')::date - 2",
)
"""The retention statements at the end of ``deploy/schema.sql``, run hourly by the engine."""

_LOGGED_NO_DATABASE = threading.Event()


# ---------------------------------------------------------------------------- SQL builders


def window_start(kind: WindowKind, now: datetime) -> datetime:
    """The UTC start of the ``kind`` window holding ``now`` (``date_trunc`` in UTC)."""
    utc = now.astimezone(UTC)
    if kind == "minute":
        return utc.replace(second=0, microsecond=0)
    if kind == "day":
        return utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class QuotaDelta:
    """What one call adds to a provider's window rows."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    neurons: float = 0.0
    gpu_seconds: float = 0.0
    rate_limited: int = 0
    errors: int = 0

    @property
    def empty(self) -> bool:
        return self == QuotaDelta()


def quota_params(provider: str, delta: QuotaDelta, now: datetime) -> list[tuple[Any, ...]]:
    """Parameters of :data:`UPSERT_QUOTA_SQL`, one tuple per window kind."""
    return [
        (
            provider,
            kind,
            window_start(kind, now),
            delta.requests,
            delta.input_tokens,
            delta.output_tokens,
            round(delta.neurons, 3),
            round(delta.gpu_seconds, 1),
            delta.rate_limited,
            delta.errors,
        )
        for kind in WINDOW_KINDS
    ]


def totals_params(now: datetime) -> tuple[datetime, datetime]:
    """Parameters of :data:`READ_TOTALS_SQL`: the current day and month starts."""
    return window_start("day", now), window_start("month", now)


def receipt_params(fact: AnswerFactObject) -> tuple[str, str]:
    """Parameters of :data:`INSERT_RECEIPT_SQL`: the answer id and the canonical fact JSON.

    Raises:
        ValueError: the fact would hold a raw query (it never does; this is a second guard).
    """
    data = fact.to_dict()
    if "query" in data or "query_text" in data:
        raise ValueError("a receipt must not hold the query")
    return fact.answer_id, canonical_json(fact).decode("utf-8")


def daily_cap_read_params(key_hash: str, day: date) -> tuple[str, date]:
    """Parameters of :data:`READ_DAILY_CAP_SQL` (requirements R3, R5)."""
    return (key_hash, day)


def daily_cap_upsert_params(key_hash: str, day: date, count: int = 1) -> tuple[str, date, int]:
    """Parameters of :data:`UPSERT_DAILY_CAP_SQL` (requirements R3, R5)."""
    return (key_hash, day, count)


def quota_delta(
    usage: CallUsage,
    before: Mapping[str, Mapping[str, float]],
    after: Mapping[str, Mapping[str, float]],
    *,
    modal_usd_per_s: float,
) -> QuotaDelta:
    """One call's usage from its :class:`CallUsage` and the in-memory ledger's snapshots taken
    just before and after it was recorded (Neurons and Modal dollars come from the ledger's
    own model, so the shared rows agree with it)."""
    sent = usage.sent
    input_tokens = usage.prompt_tokens if usage.prompt_tokens is not None else usage.est_tokens
    neurons = 0.0
    gpu_seconds = 0.0
    if usage.provider == "cloudflare":
        b, a = before["cloudflare"]["neurons_day"], after["cloudflare"]["neurons_day"]
        neurons = a - b if a >= b else a  # a lower value: the UTC day rolled over
    elif usage.provider == "modal" and modal_usd_per_s > 0:
        b, a = before["modal"]["usd_month"], after["modal"]["usd_month"]
        gpu_seconds = (a - b if a >= b else a) / modal_usd_per_s
    limited = bool(usage.block_s) or (
        usage.headers is not None and usage.headers.retry_after_s is not None
    )
    return QuotaDelta(
        requests=1 if sent else 0,
        input_tokens=max(0, input_tokens) if sent else 0,
        output_tokens=max(0, usage.completion_tokens or 0) if sent else 0,
        neurons=max(0.0, neurons),
        gpu_seconds=max(0.0, gpu_seconds),
        rate_limited=1 if sent and limited else 0,
    )


# ---------------------------------------------------------------------------- database


class _Connection(Protocol):
    def execute(self, query: Any, params: Any = ...) -> Any: ...
    def close(self) -> None: ...


Connect = Callable[[str], _Connection]


def _psycopg_connect(dsn: str) -> _Connection:
    import psycopg  # noqa: PLC0415 - imported only when a database is configured

    connection: _Connection = psycopg.connect(
        dsn, autocommit=True, connect_timeout=5, application_name="chapterverse"
    )
    return connection


@dataclass(slots=True)
class _Job:
    sql: str
    params: Sequence[Sequence[Any]]


class Database:
    """One Postgres connection behind a lock, with background writes. Thread-safe.

    ``dsn`` is never logged or shown in the repr. A failure closes the connection, is logged
    once (class name only), and the next attempt waits ``retry_s``. ``background=False`` runs
    writes inline (tests).
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect: Connect = _psycopg_connect,
        retry_s: float = 60.0,
        background: bool = True,
        max_queue: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dsn = dsn
        self._connect = connect
        self.retry_s = retry_s
        self._clock = clock
        self._lock = threading.Lock()
        self._conn: _Connection | None = None
        self._down_since: float | None = None
        self._queue: queue.Queue[_Job | None] | None = None
        self._thread: threading.Thread | None = None
        if background:
            self._queue = queue.Queue(maxsize=max_queue)
            self._thread = threading.Thread(target=self._drain, name="db-writer", daemon=True)
            self._thread.start()

    def __repr__(self) -> str:
        return f"Database(up={self.up})"

    @property
    def up(self) -> bool:
        with self._lock:
            return self._down_since is None

    def _failed(self, exc: BaseException, what: str) -> None:
        if self._down_since is None:
            log.warning(
                "database %s failed (%s); retrying in %gs", what, type(exc).__name__, self.retry_s
            )
        self._down_since = self._clock()
        if self._conn is not None:
            with contextlib.suppress(Exception):  # closing a broken connection
                self._conn.close()
            self._conn = None

    def _connection(self) -> _Connection | None:
        """The open connection, or None while waiting to retry. Holds ``_lock``."""
        if self._conn is not None:
            return self._conn
        if self._down_since is not None and self._clock() - self._down_since < self.retry_s:
            return None
        try:
            self._conn = self._connect(self._dsn)
        except Exception as exc:  # noqa: BLE001 - any driver failure means "down"
            self._failed(exc, "connect")
            return None
        if self._down_since is not None:
            log.info("database connection restored")
        self._down_since = None
        return self._conn

    def execute_many(self, sql: str, params: Sequence[Sequence[Any]]) -> bool:
        """Run ``sql`` once per parameter tuple, now. False when the database is down."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return False
            try:
                for row in params:
                    conn.execute(sql, row)
            except Exception as exc:  # noqa: BLE001
                self._failed(exc, "write")
                return False
            return True

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]] | None:
        """The rows of ``sql``, or None when the database is down."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return None
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception as exc:  # noqa: BLE001
                self._failed(exc, "read")
                return None
            return [tuple(r) for r in rows]

    def submit(self, sql: str, params: Sequence[Sequence[Any]]) -> None:
        """Queue a write (or run it now when not in background mode). Never blocks or raises:
        a full queue drops the write and logs it."""
        if self._queue is None:
            self.execute_many(sql, params)
            return
        try:
            self._queue.put_nowait(_Job(sql, params))
        except queue.Full:
            log.warning("database write queue full; a write was dropped")

    def _drain(self) -> None:
        assert self._queue is not None  # noqa: S101 - background mode
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self.execute_many(job.sql, job.params)
            finally:
                self._queue.task_done()

    def flush(self, timeout_s: float = 5.0) -> None:
        """Wait (at most ``timeout_s``) for queued writes."""
        if self._queue is None:
            return
        deadline = time.monotonic() + timeout_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    def close(self) -> None:
        """Flush queued writes, stop the writer and close the connection."""
        if self._queue is not None and self._thread is not None:
            self.flush()
            self._queue.put(None)
            self._thread.join(timeout=5.0)
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
                self._conn = None


# ---------------------------------------------------------------------------- ledger


@dataclass(slots=True)
class Totals:
    """One provider's shared totals in one window."""

    requests: int = 0
    tokens: int = 0
    neurons: float = 0.0
    gpu_seconds: float = 0.0

    def add(self, delta: QuotaDelta) -> None:
        self.requests += delta.requests
        self.tokens += delta.input_tokens + delta.output_tokens
        self.neurons += delta.neurons
        self.gpu_seconds += delta.gpu_seconds


@dataclass(slots=True)
class _Cache:
    day: str = ""
    month: str = ""
    read_at: float | None = None
    rows: dict[tuple[str, str], Totals] = field(default_factory=dict)


def _num(value: Any) -> float:
    if isinstance(value, Decimal | int | float):
        return float(value)
    return 0.0


class PostgresLedger:
    """:class:`QuotaLedger` over ``provider_quota``, layered on an in-memory ledger (module
    docstring). Thread-safe."""

    def __init__(
        self,
        db: Database,
        *,
        inner: InMemoryLedger | None = None,
        refresh_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.inner = inner if inner is not None else InMemoryLedger()
        self.refresh_s = refresh_s
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._record_lock = threading.Lock()
        self._cache = _Cache()

    def __repr__(self) -> str:
        return f"PostgresLedger({self.db!r})"

    # ------------------------------------------------------------------ protocol

    def reserve(self, provider: str, est_tokens: int, *, max_output_tokens: int = 0) -> bool:
        if not self.inner.reserve(provider, est_tokens, max_output_tokens=max_output_tokens):
            return False
        if self._shared_fits(provider, est_tokens, max_output_tokens):
            return True
        # Release the local hold: nothing will be sent.
        self.inner.record(
            CallUsage(provider, est_tokens, max_output_tokens=max_output_tokens, sent=False)
        )
        return False

    def available(self, provider: str) -> bool:
        return self.inner.available(provider) and self._shared_fits(provider, 1, 0)

    def window_wait(
        self,
        provider: str,
        est_tokens: int = 1,
        *,
        max_output_tokens: int = 0,
        purpose: Purpose | None = None,
    ) -> tuple[str, float] | None:
        eff_max_out = JUDGE_OUTPUT_RESERVE if purpose == "judge" else max_output_tokens
        if not self._shared_fits(provider, est_tokens, eff_max_out):
            now_wall = self._wall()
            if provider == "modal":
                return ("month", seconds_to_utc_month(now_wall))
            return ("day", seconds_to_utc_midnight(now_wall))
        return self.inner.window_wait(
            provider, est_tokens, max_output_tokens=max_output_tokens, purpose=purpose
        )

    refusal = window_wait

    def record(self, usage: CallUsage) -> None:
        with self._record_lock:  # the snapshot diff must be this call's alone
            before = self.inner.snapshot()
            self.inner.record(usage)
            after = self.inner.snapshot()
        limits = self.inner.modal_limits
        delta = quota_delta(
            usage, before, after, modal_usd_per_s=limits.gpu_usd_per_s * limits.overhead
        )
        if delta.empty:
            return
        now = datetime.fromtimestamp(self._wall(), UTC)
        with self._lock:
            self._roll(now)
            for kind in ("day", "month"):
                self._cache.rows.setdefault((usage.provider, kind), Totals()).add(delta)
        self.db.submit(UPSERT_QUOTA_SQL, quota_params(usage.provider, delta, now))

    # ------------------------------------------------------------------ shared limits

    def _roll(self, now: datetime) -> None:
        """Start empty totals when the UTC day or month changed. Holds ``_lock``."""
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        if (day, month) != (self._cache.day, self._cache.month):
            self._cache = _Cache(day=day, month=month)

    def totals(self, provider: str, kind: Literal["day", "month"]) -> Totals:
        """The shared totals as last read (plus this process's writes since)."""
        self._refresh()
        with self._lock:
            found = self._cache.rows.get((provider, kind))
            return Totals(**{k: getattr(found, k) for k in Totals.__slots__}) if found else Totals()

    def _refresh(self) -> None:
        now = datetime.fromtimestamp(self._wall(), UTC)
        with self._lock:
            self._roll(now)
            read_at = self._cache.read_at
            if read_at is not None and self._clock() - read_at < self.refresh_s:
                return
            self._cache.read_at = self._clock()  # one reader at a time; others use the cache
        rows = self.db.fetchall(READ_TOTALS_SQL, totals_params(now))
        if rows is None:
            return
        fresh: dict[tuple[str, str], Totals] = {}
        for provider, kind, requests, input_tokens, output_tokens, neurons, gpu in rows:
            fresh[(str(provider), str(kind))] = Totals(
                requests=int(requests),
                tokens=int(input_tokens) + int(output_tokens),
                neurons=_num(neurons),
                gpu_seconds=_num(gpu),
            )
        with self._lock:
            if self._cache.day == now.strftime("%Y-%m-%d"):
                self._cache.rows = fresh

    def _shared_fits(self, provider: str, est_tokens: int, max_output_tokens: int) -> bool:
        if provider not in ("groq", "cloudflare", "modal"):
            return True
        if provider == "groq":
            day = self.totals("groq", "day")
            lim = self.inner.groq_limits
            return (
                day.requests + 1 <= lim.requests_per_day
                and day.tokens + est_tokens + max_output_tokens <= lim.tokens_per_day
            )
        if provider == "cloudflare":
            day = self.totals("cloudflare", "day")
            cf = self.inner.cloudflare_limits
            need = (
                est_tokens * cf.neurons_per_m_input + max_output_tokens * cf.neurons_per_m_output
            ) / 1e6
            return day.neurons + need <= cf.neurons_per_day
        month = self.totals("modal", "month")
        modal = self.inner.modal_limits
        return month.gpu_seconds * modal.gpu_usd_per_s * modal.overhead < modal.usd_per_month


# ---------------------------------------------------------------------------- receipts


class ReceiptStore:
    """Stores each Answer Fact Object under its ``answer_id`` (``receipts`` table)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def __repr__(self) -> str:
        return f"ReceiptStore({self.db!r})"

    def save(self, fact: AnswerFactObject) -> None:
        """Queue the receipt. Never raises for a database failure."""
        self.db.submit(INSERT_RECEIPT_SQL, [receipt_params(fact)])

    def purge(self) -> bool:
        """Run the retention statements now. False when the database is down."""
        return all(self.db.execute_many(sql, [()]) for sql in PURGE_SQL)


class PostgresDailyCap:
    """:class:`~chapterverse.ratelimit.DailyCapStore` over ``daily_caps`` (requirements R3).
    Thread-safe."""

    def __init__(self, db: Database, *, inner: InMemoryDailyCap | None = None) -> None:
        self.db = db
        self.inner = inner if inner is not None else InMemoryDailyCap()

    def __repr__(self) -> str:
        return f"PostgresDailyCap({self.db!r})"

    def get(self, key_hash: str, day: date) -> int:
        rows = self.db.fetchall(READ_DAILY_CAP_SQL, daily_cap_read_params(key_hash, day))
        if rows is not None:
            if rows:
                db_val = int(rows[0][0])
                local_val = self.inner.get(key_hash, day)
                return max(db_val, local_val)
            return self.inner.get(key_hash, day)
        return self.inner.get(key_hash, day)

    def increment(self, key_hash: str, day: date) -> int:
        val = self.inner.increment(key_hash, day)
        self.db.submit(UPSERT_DAILY_CAP_SQL, [daily_cap_upsert_params(key_hash, day, 1)])
        return val


@dataclass(frozen=True, slots=True)
class Persistence:
    """What :func:`open_persistence` gives the app."""

    ledger: QuotaLedger
    receipts: ReceiptStore | None
    database: Database | None
    daily_caps: DailyCapStore | None = None

    def close(self) -> None:
        if self.database is not None:
            self.database.close()


def open_persistence(
    settings: Settings, *, connect: Connect | None = None, background: bool = True
) -> Persistence:
    """The Postgres ledger and receipt store, or (no ``DATABASE_URL``) an in-memory ledger
    and no receipt store. Opening never fails: the first connection is lazy."""
    if not settings.database_url:
        if not _LOGGED_NO_DATABASE.is_set():
            _LOGGED_NO_DATABASE.set()
            log.info("DATABASE_URL is not set: quota ledger in memory, receipts not stored")
        return Persistence(
            ledger=InMemoryLedger(),
            receipts=None,
            database=None,
            daily_caps=InMemoryDailyCap(),
        )
    db = Database(settings.database_url, connect=connect or _psycopg_connect, background=background)
    return Persistence(
        ledger=PostgresLedger(db),
        receipts=ReceiptStore(db),
        database=db,
        daily_caps=PostgresDailyCap(db),
    )
