"""Per-client token bucket for ``POST /api/ask`` (plan section 5, stage "Edge").

The Worker rate-limits each IP first (10 questions a minute); this bucket is the second line, in
the engine, so a request that reaches the origin some other way is limited too. It runs before
routing: a refused request costs no lookup and no provider call.

Each key (a client IP) gets ``burst`` tokens that refill at ``per_minute`` a minute. Keys are held
in memory only, never logged or stored; full buckets are dropped when the table passes
``max_keys``, so memory stays bounded whatever the traffic.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol

from chapterverse.contracts import sha256_hex

__all__ = [
    "DEFAULT_BURST",
    "DEFAULT_DAILY_ASK_CAP",
    "DEFAULT_GLOBAL_DAILY_ANSWERS",
    "DEFAULT_HOSTING_SHARE",
    "DEFAULT_PER_MINUTE",
    "DailyBudgetCounts",
    "DailyBudgetStore",
    "DailyCapStore",
    "Decision",
    "InMemoryDailyBudget",
    "InMemoryDailyCap",
    "TokenBucket",
    "client_key_hash",
]

DEFAULT_PER_MINUTE: Final = 10.0
"""The Worker's ``ASK_LIMITER``: 10 POSTs per 60 s per IP (deploy/README.md section 5)."""
DEFAULT_BURST: Final = 10
DEFAULT_MAX_KEYS: Final = 50_000
DEFAULT_DAILY_ASK_CAP: Final = 40
DEFAULT_GLOBAL_DAILY_ANSWERS: Final = 400
DEFAULT_HOSTING_SHARE: Final = 0.2
RETENTION_DAYS: Final = 2


def client_key_hash(client_ip: str, salt: str = "") -> str:
    """The daily cap client key: ``sha256(salt + client_ip)`` (requirements R2).

    The raw IP is never stored or logged.
    """
    return sha256_hex(salt + client_ip)


class DailyCapStore(Protocol):
    """Storage for daily answer counts per client key hash and UTC day."""

    def get(self, key_hash: str, day: date) -> int: ...
    def increment(self, key_hash: str, day: date) -> int: ...


@dataclass(frozen=True, slots=True)
class DailyBudgetCounts:
    """Answer counts for a UTC day across all users."""

    total: int = 0
    hosting: int = 0


class DailyBudgetStore(Protocol):
    """Storage for daily answer counts across all users and UTC day."""

    def get(self, day: date) -> DailyBudgetCounts: ...
    def increment(self, day: date, *, hosting: bool = False) -> DailyBudgetCounts: ...


class InMemoryDailyBudget:
    """In-memory daily answer counts across all users and UTC day. Thread-safe."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._counts: dict[date, DailyBudgetCounts] = {}
        self._clock = clock

    def __repr__(self) -> str:
        with self._lock:
            return f"InMemoryDailyBudget(days={len(self._counts)})"

    def __len__(self) -> int:
        with self._lock:
            return len(self._counts)

    def get(self, day: date) -> DailyBudgetCounts:
        with self._lock:
            return self._counts.get(day, DailyBudgetCounts())

    def increment(self, day: date, *, hosting: bool = False) -> DailyBudgetCounts:
        with self._lock:
            curr = self._counts.get(day, DailyBudgetCounts())
            val = DailyBudgetCounts(
                total=curr.total + 1,
                hosting=curr.hosting + (1 if hosting else 0),
            )
            self._counts[day] = val
            self._prune(day)
            return val

    def _prune(self, current_day: date) -> None:
        """Drop entries older than 2 days."""
        old = [d for d in self._counts if (current_day - d).days > RETENTION_DAYS]
        for d in old:
            del self._counts[d]


class InMemoryDailyCap:
    """In-memory daily answer counts per client key hash and UTC day. Thread-safe."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, date], int] = {}
        self._clock = clock

    def __repr__(self) -> str:
        with self._lock:
            return f"InMemoryDailyCap(entries={len(self._counts)})"

    def __len__(self) -> int:
        with self._lock:
            return len(self._counts)

    def get(self, key_hash: str, day: date) -> int:
        with self._lock:
            return self._counts.get((key_hash, day), 0)

    def increment(self, key_hash: str, day: date) -> int:
        with self._lock:
            val = self._counts.get((key_hash, day), 0) + 1
            self._counts[(key_hash, day)] = val
            self._prune(day)
            return val

    def _prune(self, current_day: date) -> None:
        """Drop entries older than 2 days."""
        old = [k for k in self._counts if (current_day - k[1]).days > RETENTION_DAYS]
        for k in old:
            del self._counts[k]


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a request may go, and how long to wait when it may not (``Retry-After``)."""

    allowed: bool
    retry_after_s: float = 0.0

    @property
    def retry_after_header(self) -> str:
        """Whole seconds, at least 1, for the ``Retry-After`` header."""
        return str(max(1, math.ceil(self.retry_after_s)))


class TokenBucket:
    """A token bucket per key. Thread-safe; ``clock`` returns seconds (monotonic)."""

    def __init__(
        self,
        *,
        per_minute: float = DEFAULT_PER_MINUTE,
        burst: int = DEFAULT_BURST,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute <= 0 or burst < 1 or max_keys < 1:
            raise ValueError("per_minute, burst and max_keys must be positive")
        self.rate_per_s = per_minute / 60.0
        self.burst = float(burst)
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, updated)

    def __repr__(self) -> str:
        return f"TokenBucket(per_minute={self.rate_per_s * 60:g}, burst={self.burst:g})"

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _level(self, key: str, now: float) -> float:
        tokens, updated = self._buckets.get(key, (self.burst, now))
        return min(self.burst, tokens + (now - updated) * self.rate_per_s)

    def take(self, key: str) -> Decision:
        """Spend one token for ``key``; refused (with the wait) when none is left."""
        with self._lock:
            now = self._clock()
            tokens = self._level(key, now)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return Decision(allowed=False, retry_after_s=(1.0 - tokens) / self.rate_per_s)
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > self.max_keys:
                self._prune(now)
            return Decision(allowed=True)

    def _prune(self, now: float) -> None:
        """Drop buckets that have refilled (they hold no state worth keeping); if that is not
        enough, drop the oldest half."""
        full = [k for k in self._buckets if self._level(k, now) >= self.burst]
        for key in full:
            del self._buckets[key]
        if len(self._buckets) > self.max_keys:
            by_age = sorted(self._buckets, key=lambda k: self._buckets[k][1])
            for key in by_age[: len(by_age) // 2]:
                del self._buckets[key]
