"""The quota ledger: free-tier limits per provider, checked before a call and updated after it.

:class:`QuotaLedger` is the storage-agnostic Protocol (a Postgres version follows);
:class:`InMemoryLedger` is a thread-safe in-process implementation with an injectable clock.

Limits (decision 0001, item 9):

* Groq: 1,000 requests a day and 8,000 tokens a minute (live headers), with 30 requests a
  minute and 200K tokens a day as unconfirmed ceilings. ``x-ratelimit-*`` response headers,
  when present, override the local count until their reset time.
* Cloudflare: Neurons per UTC day against a budget of 9,500 of the free 10,000.
* Modal: dollars per UTC month against a cap (default $30), and an optional lower session cap.
  Modal bills container time, not tokens, so the cost is modelled from a measured cold start,
  the scaledown window and call wall time (:class:`ModalLimits`).

Token estimate: :data:`TOKENS_PER_CHAR` = 1.5 / 4, i.e. 4 characters per token with a 1.5x
safety margin (the rule of ``product-answer-checks/scripts/router.py``). A reservation also
counts the request's ``max_tokens`` as worst-case output.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final, Protocol, runtime_checkable

from chapterverse.contracts import ChatMessage

__all__ = [
    "TOKENS_PER_CHAR",
    "CallUsage",
    "CloudflareLimits",
    "GroqLimits",
    "InMemoryLedger",
    "ModalLimits",
    "QuotaLedger",
    "RateHeaders",
    "estimate_tokens",
    "parse_duration",
    "parse_rate_headers",
    "parse_retry_after",
    "seconds_to_utc_midnight",
]

TOKENS_PER_CHAR: Final = 1.5 / 4
"""Estimated tokens per character of message text: 4 characters a token, x1.5 margin."""


def estimate_tokens(messages: Sequence[ChatMessage], prefill: str | None = None) -> int:
    """Estimated prompt tokens: ``ceil(characters * TOKENS_PER_CHAR)``, plus 4 per message."""
    chars = sum(len(m["content"]) for m in messages) + len(prefill or "")
    return math.ceil(chars * TOKENS_PER_CHAR) + 4 * (len(messages) + (1 if prefill else 0))


def seconds_to_utc_midnight(now: float) -> float:
    """Seconds from epoch time ``now`` to the next 00:00 UTC."""
    current = datetime.fromtimestamp(now, UTC)
    midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - current).total_seconds()


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")


def _utc_month(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class RateHeaders:
    """Groq's ``x-ratelimit-*`` and ``retry-after`` response headers, parsed. None when absent.

    For Groq ``limit_requests`` is the per-day request limit and ``limit_tokens`` the per-minute
    token limit (live, 2026-11-18). Reset values are seconds from the response.
    """

    limit_requests: int | None = None
    remaining_requests: int | None = None
    reset_requests_s: float | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    retry_after_s: float | None = None


@dataclass(frozen=True, slots=True)
class CallUsage:
    """What one call used, reported by the provider client after the call.

    ``est_tokens`` and ``max_output_tokens`` must equal what was passed to
    :meth:`QuotaLedger.reserve`: they release that reservation. ``sent`` is False when the request
    never left (only the reservation is released). Token counts are None when the provider did
    not report them; the estimate then stands in. ``block_s`` is a provider-reported wait (a
    daily quota exhausted) during which the ledger refuses this provider.
    """

    provider: str
    est_tokens: int
    max_output_tokens: int = 0
    sent: bool = True
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    neurons: float | None = None
    latency_s: float | None = None
    usd: float | None = None
    headers: RateHeaders | None = None
    block_s: float | None = None


@runtime_checkable
class QuotaLedger(Protocol):
    """Free-tier accounting shared by every request. Implementations must be thread-safe."""

    def reserve(self, provider: str, est_tokens: int, *, max_output_tokens: int = 0) -> bool:
        """Hold room for one call; False (holding nothing) when it would pass a limit."""
        ...

    def record(self, usage: CallUsage) -> None:
        """Release the call's reservation and add what it used."""
        ...

    def available(self, provider: str) -> bool:
        """Whether a minimal call could be reserved now (holds nothing)."""
        ...


@dataclass(frozen=True, slots=True)
class GroqLimits:
    requests_per_day: int = 1000
    tokens_per_minute: int = 8000
    requests_per_minute: int = 30
    """Unconfirmed: the docs table is labelled Developer plan."""
    tokens_per_day: int = 200_000
    """Unconfirmed: the docs table is labelled Developer plan."""


@dataclass(frozen=True, slots=True)
class CloudflareLimits:
    neurons_per_day: float = 9500.0
    """Budget, of the 10,000 free Neurons a day (reset 00:00 UTC)."""
    neurons_per_m_input: float = 40909.0
    neurons_per_m_output: float = 290909.0


@dataclass(frozen=True, slots=True)
class ModalLimits:
    """Modal spend model. A call that finds the container down pays a cold start plus the
    scaledown tail; a call while it is up extends the tail. Rates from modal.com/pricing."""

    usd_per_month: float = 30.0
    usd_per_session: float | None = None
    gpu_usd_per_s: float = 0.000542
    """L40S."""
    overhead: float = 1.15
    """CPU and memory on top of the GPU (the verifier ledger's 15 %)."""
    cold_start_s: float = 406.0
    """Measured, legal-rag-verifier GRID stop 78."""
    scaledown_s: float = 120.0


@dataclass(slots=True)
class _Groq:
    minute: deque[tuple[float, int]] = field(default_factory=deque)
    day: deque[tuple[float, int]] = field(default_factory=deque)
    pending_requests: int = 0
    pending_tokens: int = 0
    hdr_requests: int | None = None
    hdr_requests_until: float = 0.0
    hdr_tokens: int | None = None
    hdr_tokens_until: float = 0.0
    blocked_until: float = 0.0


@dataclass(slots=True)
class _Cloudflare:
    day: str = ""
    neurons: float = 0.0
    pending: float = 0.0
    blocked_until: float = 0.0


@dataclass(slots=True)
class _Modal:
    month: str = ""
    month_usd: float = 0.0
    session_usd: float = 0.0
    inflight: int = 0
    billed_until: float | None = None
    blocked_until: float = 0.0


@dataclass(slots=True)
class _Other:
    blocked_until: float = 0.0


class InMemoryLedger:
    """:class:`QuotaLedger` in process memory. ``clock`` returns epoch seconds (UTC resets).

    Providers other than ``groq``, ``cloudflare`` and ``modal`` have no limits, only blocks.
    """

    def __init__(
        self,
        *,
        groq: GroqLimits | None = None,
        cloudflare: CloudflareLimits | None = None,
        modal: ModalLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.groq_limits = groq or GroqLimits()
        self.cloudflare_limits = cloudflare or CloudflareLimits()
        self.modal_limits = modal or ModalLimits()
        self._clock = clock
        self._lock = threading.Lock()
        self._groq = _Groq()
        self._cf = _Cloudflare()
        self._modal = _Modal()
        self._other: dict[str, _Other] = {}
        # Limits learned from headers override the defaults.
        self._groq_rpd = self.groq_limits.requests_per_day
        self._groq_tpm = self.groq_limits.tokens_per_minute

    def __repr__(self) -> str:
        return "InMemoryLedger()"

    # ------------------------------------------------------------------ protocol

    def reserve(self, provider: str, est_tokens: int, *, max_output_tokens: int = 0) -> bool:
        with self._lock:
            now = self._clock()
            if not self._fits(provider, now, est_tokens, max_output_tokens):
                return False
            self._hold(provider, est_tokens, max_output_tokens, sign=1)
            return True

    def available(self, provider: str) -> bool:
        with self._lock:
            return self._fits(provider, self._clock(), 1, 0)

    def record(self, usage: CallUsage) -> None:
        with self._lock:
            now = self._clock()
            self._hold(usage.provider, usage.est_tokens, usage.max_output_tokens, sign=-1)
            if usage.provider == "groq":
                self._record_groq(usage, now)
            elif usage.provider == "cloudflare":
                self._record_cf(usage, now)
            elif usage.provider == "modal":
                self._record_modal(usage, now)
            if usage.block_s is not None and usage.block_s > 0:
                state = self._blockable(usage.provider)
                state.blocked_until = max(state.blocked_until, now + usage.block_s)

    # ------------------------------------------------------------------ inspection

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Current counts per provider (for logs and tests). Never holds a secret."""
        with self._lock:
            now = self._clock()
            self._prune_groq(now)
            self._roll_cf(now)
            self._roll_modal(now)
            g, c, m = self._groq, self._cf, self._modal
            return {
                "groq": {
                    "requests_minute": float(len(g.minute)),
                    "requests_day": float(len(g.day)),
                    "tokens_minute": float(sum(t for _, t in g.minute)),
                    "tokens_day": float(sum(t for _, t in g.day)),
                    "pending_requests": float(g.pending_requests),
                    "pending_tokens": float(g.pending_tokens),
                    "requests_per_day": float(self._groq_rpd),
                    "tokens_per_minute": float(self._groq_tpm),
                },
                "cloudflare": {"neurons_day": c.neurons, "pending_neurons": c.pending},
                "modal": {
                    "usd_month": m.month_usd,
                    "usd_session": m.session_usd,
                    "pending_usd": m.inflight * self._modal_worst(),
                },
            }

    # ------------------------------------------------------------------ internals

    def _blockable(self, provider: str) -> _Groq | _Cloudflare | _Modal | _Other:
        if provider == "groq":
            return self._groq
        if provider == "cloudflare":
            return self._cf
        if provider == "modal":
            return self._modal
        return self._other.setdefault(provider, _Other())

    def _fits(self, provider: str, now: float, est: int, max_out: int) -> bool:
        if now < self._blockable(provider).blocked_until:
            return False
        if provider == "groq":
            return self._groq_fits(now, est + max_out)
        if provider == "cloudflare":
            self._roll_cf(now)
            need = self._neurons(est, max_out)
            budget = self.cloudflare_limits.neurons_per_day
            return self._cf.neurons + self._cf.pending + need <= budget
        if provider == "modal":
            self._roll_modal(now)
            need = self._modal_estimate(now)
            m, lim = self._modal, self.modal_limits
            held = m.inflight * self._modal_worst()
            if m.month_usd + held + need > lim.usd_per_month:
                return False
            return lim.usd_per_session is None or m.session_usd + held + need <= (
                lim.usd_per_session
            )
        return True

    def _hold(self, provider: str, est: int, max_out: int, *, sign: int) -> None:
        if provider == "groq":
            g = self._groq
            g.pending_requests = max(0, g.pending_requests + sign)
            g.pending_tokens = max(0, g.pending_tokens + sign * (est + max_out))
        elif provider == "cloudflare":
            self._cf.pending = max(0.0, self._cf.pending + sign * self._neurons(est, max_out))
        elif provider == "modal":
            # Each call in flight holds the worst case (a cold start and its tail).
            self._modal.inflight = max(0, self._modal.inflight + sign)

    # Groq -------------------------------------------------------------

    def _prune_groq(self, now: float) -> None:
        g = self._groq
        while g.minute and g.minute[0][0] <= now - 60.0:
            g.minute.popleft()
        while g.day and g.day[0][0] <= now - 86400.0:
            g.day.popleft()

    def _groq_fits(self, now: float, tokens: int) -> bool:
        self._prune_groq(now)
        g, lim = self._groq, self.groq_limits
        requests_minute = len(g.minute) + g.pending_requests + 1
        requests_day = len(g.day) + g.pending_requests + 1
        tokens_minute = sum(t for _, t in g.minute) + g.pending_tokens + tokens
        tokens_day = sum(t for _, t in g.day) + g.pending_tokens + tokens
        if requests_day > self._groq_rpd or requests_minute > lim.requests_per_minute:
            return False
        if tokens_minute > self._groq_tpm or tokens_day > lim.tokens_per_day:
            return False
        if (
            g.hdr_requests is not None
            and now < g.hdr_requests_until
            and g.hdr_requests - g.pending_requests < 1
        ):
            return False
        return not (
            g.hdr_tokens is not None
            and now < g.hdr_tokens_until
            and g.hdr_tokens - g.pending_tokens < tokens
        )

    def _record_groq(self, usage: CallUsage, now: float) -> None:
        g = self._groq
        if usage.sent:
            if usage.prompt_tokens is None and usage.completion_tokens is None:
                used = usage.est_tokens
            else:
                used = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
            g.minute.append((now, used))
            g.day.append((now, used))
            if g.hdr_requests is not None:
                g.hdr_requests -= 1
            if g.hdr_tokens is not None:
                g.hdr_tokens -= used
        h = usage.headers
        if h is None:
            return
        if h.limit_requests is not None:
            self._groq_rpd = h.limit_requests
        if h.limit_tokens is not None:
            self._groq_tpm = h.limit_tokens
        if h.remaining_requests is not None:
            g.hdr_requests = h.remaining_requests
            g.hdr_requests_until = now + (h.reset_requests_s or 60.0)
        if h.remaining_tokens is not None:
            g.hdr_tokens = h.remaining_tokens
            g.hdr_tokens_until = now + (h.reset_tokens_s or 60.0)

    # Cloudflare -------------------------------------------------------

    def _neurons(self, est: int, max_out: int) -> float:
        lim = self.cloudflare_limits
        return (est * lim.neurons_per_m_input + max_out * lim.neurons_per_m_output) / 1e6

    def _roll_cf(self, now: float) -> None:
        day = _utc_day(now)
        if day != self._cf.day:
            self._cf.day = day
            self._cf.neurons = 0.0

    def _record_cf(self, usage: CallUsage, now: float) -> None:
        self._roll_cf(now)
        if not usage.sent:
            return
        if usage.neurons is not None:
            spent = usage.neurons
        else:
            prompt = usage.prompt_tokens if usage.prompt_tokens is not None else usage.est_tokens
            out = usage.completion_tokens
            spent = self._neurons(prompt, usage.max_output_tokens if out is None else out)
        self._cf.neurons += max(0.0, spent)

    # Modal ------------------------------------------------------------

    def _roll_modal(self, now: float) -> None:
        month = _utc_month(now)
        if month != self._modal.month:
            self._modal.month = month
            self._modal.month_usd = 0.0

    def _rate(self) -> float:
        lim = self.modal_limits
        return lim.gpu_usd_per_s * lim.overhead

    def _modal_estimate(self, now: float) -> float:
        lim = self.modal_limits
        if self._modal.billed_until is not None and now <= self._modal.billed_until:
            return lim.scaledown_s * self._rate()  # at most one more tail
        return (lim.cold_start_s + lim.scaledown_s) * self._rate()

    def _modal_worst(self) -> float:
        lim = self.modal_limits
        return (lim.cold_start_s + lim.scaledown_s) * self._rate()

    def _record_modal(self, usage: CallUsage, now: float) -> None:
        self._roll_modal(now)
        if not usage.sent:
            return
        m, lim = self._modal, self.modal_limits
        if usage.usd is not None:
            cost = usage.usd
        else:
            start = now - (usage.latency_s or 0.0)
            if m.billed_until is None or start > m.billed_until:
                # The container was down: this call started it (cold start, then the tail).
                until = max(now, start + lim.cold_start_s) + lim.scaledown_s
                cost = (until - start) * self._rate()
                m.billed_until = until
            else:
                until = now + lim.scaledown_s
                cost = max(0.0, until - m.billed_until) * self._rate()
                m.billed_until = max(m.billed_until, until)
        m.month_usd += cost
        m.session_usd += cost


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(value: str | None) -> float | None:
    """Seconds in a Groq reset value (``"2m59.56s"``, ``"7.66s"``, ``"120ms"``, ``"1h2m"``) or a
    bare number of seconds; None when absent or unparsable."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * scale[u] for n, u in parts)


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds in a ``Retry-After`` header: a number of seconds or an HTTP date."""
    if value is None or not value.strip():
        return None
    seconds = parse_duration(value)
    if seconds is not None:
        return seconds
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def parse_rate_headers(headers: Mapping[str, str]) -> RateHeaders | None:
    """Groq-style ``x-ratelimit-*`` and ``retry-after`` headers; None when none is present."""

    def integer(name: str) -> int | None:
        value = headers.get(name)
        try:
            return None if value is None else int(float(value))
        except ValueError:
            return None

    out = RateHeaders(
        limit_requests=integer("x-ratelimit-limit-requests"),
        remaining_requests=integer("x-ratelimit-remaining-requests"),
        reset_requests_s=parse_duration(headers.get("x-ratelimit-reset-requests")),
        limit_tokens=integer("x-ratelimit-limit-tokens"),
        remaining_tokens=integer("x-ratelimit-remaining-tokens"),
        reset_tokens_s=parse_duration(headers.get("x-ratelimit-reset-tokens")),
        retry_after_s=parse_retry_after(headers.get("retry-after")),
    )
    return None if out == RateHeaders() else out
