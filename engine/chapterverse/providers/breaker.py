"""A thread-safe circuit breaker, one per provider (plan §5 stage 7).

States: ``closed`` (calls pass), ``open`` (calls refused until the cooldown ends), ``half_open``
(the cooldown ended: one probe call at a time may pass; its success closes the circuit, its
failure opens it again). ``trip(seconds)`` opens the circuit for a stated time, for a provider
that said when it will be back (HTTP 429 ``Retry-After``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Literal, TypeAlias

__all__ = ["BreakerState", "CircuitBreaker"]

BreakerState: TypeAlias = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Opens after ``failure_threshold`` consecutive failures, for ``cooldown_s`` seconds.

    ``clock`` returns seconds (default ``time.monotonic``). Every method is thread-safe.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_s < 0:
            raise ValueError("cooldown_s must not be negative")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be at least 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.half_open_max_calls = half_open_max_calls
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until: float | None = None
        self._probes = 0

    def __repr__(self) -> str:
        return f"CircuitBreaker({self.name!r}, state={self.state!r})"

    def _state(self, now: float) -> BreakerState:
        if self._open_until is None:
            return "closed"
        if now < self._open_until:
            return "open"
        return "half_open"

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state(self._clock())

    def retry_after_s(self) -> float | None:
        """Seconds until an open circuit half-opens; None when it is not open."""
        with self._lock:
            now = self._clock()
            if self._open_until is None or now >= self._open_until:
                return None
            return self._open_until - now

    def allow(self) -> bool:
        """Whether a call may go now. In half-open this takes a probe slot: report the outcome
        with :meth:`record_success`, :meth:`record_failure` or :meth:`cancel`."""
        with self._lock:
            state = self._state(self._clock())
            if state == "closed":
                return True
            if state == "open":
                return False
            if self._probes >= self.half_open_max_calls:
                return False
            self._probes += 1
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None
            self._probes = 0

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            if self._state(now) == "half_open":
                self._open_until = now + self.cooldown_s
                self._probes = 0
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = now + self.cooldown_s
                self._probes = 0

    def cancel(self) -> None:
        """The call allowed by :meth:`allow` was not made: free its half-open probe slot."""
        with self._lock:
            self._probes = max(0, self._probes - 1)

    def trip(self, seconds: float) -> None:
        """Open now for ``seconds`` (at least), whatever the failure count."""
        with self._lock:
            until = self._clock() + max(0.0, seconds)
            if self._open_until is None or until > self._open_until:
                self._open_until = until
            self._probes = 0
