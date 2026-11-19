"""The quota ledger (windows, headers, Neurons, Modal dollars, UTC resets) and the breaker."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from chapterverse.contracts import ChatMessage
from chapterverse.providers.breaker import CircuitBreaker
from chapterverse.providers.ledger import (
    TOKENS_PER_CHAR,
    CallUsage,
    CloudflareLimits,
    GroqLimits,
    InMemoryLedger,
    ModalLimits,
    QuotaLedger,
    RateHeaders,
    estimate_tokens,
    parse_duration,
    parse_rate_headers,
    parse_retry_after,
    seconds_to_utc_midnight,
)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def state(breaker: CircuitBreaker) -> str:
    """Read through a call so mypy does not narrow the property across assertions."""
    return breaker.state


def call(ledger: InMemoryLedger, provider: str, est: int = 10, **kw: object) -> bool:
    """Reserve and, when granted, record the call."""
    if not ledger.reserve(provider, est):
        return False
    ledger.record(CallUsage(provider=provider, est_tokens=est, **kw))  # type: ignore[arg-type]
    return True


# ---------------------------------------------------------------------------- estimates


def test_token_estimate_ratio() -> None:
    assert TOKENS_PER_CHAR == 0.375
    messages = (ChatMessage(role="user", content="x" * 400),)
    assert estimate_tokens(messages) == 150 + 4
    assert estimate_tokens(messages, "y" * 40) == 165 + 8


def test_durations_and_retry_after() -> None:
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration("1h2m3s") == pytest.approx(3723)
    assert parse_duration("12") == 12.0
    assert parse_duration("soon") is None
    assert parse_duration(None) is None
    assert parse_retry_after("30") == 30.0
    now = utc("2026-11-19T12:00:00")
    assert parse_retry_after("Thu, 19 Nov 2026 12:01:00 GMT", now) == pytest.approx(60)
    assert parse_retry_after("garbage") is None
    assert parse_rate_headers({}) is None
    parsed = parse_rate_headers(
        {"x-ratelimit-limit-requests": "1000", "x-ratelimit-reset-tokens": "7.66s"}
    )
    assert parsed is not None
    assert parsed.limit_requests == 1000
    assert parsed.reset_tokens_s == pytest.approx(7.66)
    assert parsed.remaining_requests is None


def test_seconds_to_utc_midnight() -> None:
    assert seconds_to_utc_midnight(utc("2026-11-19T23:59:00")) == pytest.approx(60)
    assert seconds_to_utc_midnight(utc("2026-11-19T00:00:00")) == pytest.approx(86400)


def test_protocol() -> None:
    assert isinstance(InMemoryLedger(), QuotaLedger)


# ---------------------------------------------------------------------------- Groq


def test_groq_requests_per_minute_window() -> None:
    clock = Clock()
    ledger = InMemoryLedger(groq=GroqLimits(requests_per_minute=3), clock=clock)
    assert [call(ledger, "groq") for _ in range(4)] == [True, True, True, False]
    clock.now += 59.9
    assert not ledger.available("groq")
    clock.now += 0.2
    assert ledger.available("groq")


def test_groq_tokens_per_minute_counts_actuals_and_reservations() -> None:
    clock = Clock()
    ledger = InMemoryLedger(groq=GroqLimits(tokens_per_minute=1000), clock=clock)
    assert ledger.reserve("groq", 300, max_output_tokens=200)  # holds 500
    assert ledger.reserve("groq", 400)  # holds 900
    assert not ledger.reserve("groq", 200)  # 1100 > 1000
    ledger.record(
        CallUsage(
            provider="groq",
            est_tokens=300,
            max_output_tokens=200,
            prompt_tokens=100,
            completion_tokens=20,
        )
    )
    assert ledger.reserve("groq", 200)  # 120 + 400 + 200
    snap = ledger.snapshot()["groq"]
    assert snap["tokens_minute"] == 120
    assert snap["pending_tokens"] == 600


def test_groq_requests_per_day_window() -> None:
    clock = Clock()
    ledger = InMemoryLedger(
        groq=GroqLimits(requests_per_day=5, requests_per_minute=100), clock=clock
    )
    for _ in range(5):
        assert call(ledger, "groq")
        clock.now += 61
    assert not call(ledger, "groq")
    clock.now += 86400 - 5 * 61 + 1  # the first call leaves the 24 h window
    assert call(ledger, "groq")


def test_groq_unconfirmed_tokens_per_day_ceiling() -> None:
    clock = Clock()
    ledger = InMemoryLedger(
        groq=GroqLimits(tokens_per_day=1000, tokens_per_minute=10_000), clock=clock
    )
    ledger.record(CallUsage(provider="groq", est_tokens=0, prompt_tokens=990, completion_tokens=0))
    assert not ledger.reserve("groq", 20)
    clock.now += 86401
    assert ledger.reserve("groq", 20)


def test_groq_headers_override_local_counts() -> None:
    clock = Clock()
    ledger = InMemoryLedger(clock=clock)
    headers = RateHeaders(
        limit_requests=1000,
        remaining_requests=1,
        reset_requests_s=30.0,
        limit_tokens=8000,
        remaining_tokens=500,
        reset_tokens_s=10.0,
    )
    ledger.record(CallUsage(provider="groq", est_tokens=0, sent=False, headers=headers))
    assert not ledger.reserve("groq", 600)  # header says 500 tokens left
    assert ledger.reserve("groq", 100)  # the last request
    assert not ledger.reserve("groq", 10)  # header says no requests left, one in flight
    clock.now += 11
    clock.now += 20
    assert ledger.reserve("groq", 10)


def test_header_limits_replace_defaults() -> None:
    ledger = InMemoryLedger(clock=Clock())
    ledger.record(
        CallUsage(
            provider="groq",
            est_tokens=0,
            sent=False,
            headers=RateHeaders(limit_requests=14400, limit_tokens=6000),
        )
    )
    snap = ledger.snapshot()["groq"]
    assert (snap["requests_per_day"], snap["tokens_per_minute"]) == (14400, 6000)


def test_unsent_call_only_releases() -> None:
    ledger = InMemoryLedger(clock=Clock())
    assert ledger.reserve("groq", 100, max_output_tokens=50)
    ledger.record(CallUsage(provider="groq", est_tokens=100, max_output_tokens=50, sent=False))
    snap = ledger.snapshot()["groq"]
    assert snap["requests_day"] == 0
    assert snap["pending_tokens"] == 0
    assert snap["pending_requests"] == 0


def test_estimate_stands_in_for_missing_usage() -> None:
    ledger = InMemoryLedger(clock=Clock())
    call(ledger, "groq", est=77)
    assert ledger.snapshot()["groq"]["tokens_minute"] == 77


def test_block_from_provider() -> None:
    clock = Clock()
    ledger = InMemoryLedger(clock=clock)
    ledger.record(CallUsage(provider="groq", est_tokens=0, block_s=100.0))
    assert not ledger.available("groq")
    clock.now += 101
    assert ledger.available("groq")
    ledger.record(CallUsage(provider="other", est_tokens=0, block_s=5.0))
    assert not ledger.available("other")
    assert ledger.available("unknown")


# ---------------------------------------------------------------------------- Cloudflare


def test_cloudflare_neurons_budget_and_utc_reset() -> None:
    clock = Clock(utc("2026-11-19T23:59:00"))
    ledger = InMemoryLedger(clock=clock)
    ledger.record(CallUsage(provider="cloudflare", est_tokens=0, neurons=9490.0))
    assert ledger.available("cloudflare")  # 1 input token is ~0.04 Neurons
    # 1000 input tokens (~41 Neurons) would pass the 9,500 budget
    assert not ledger.reserve("cloudflare", 1000)
    assert ledger.reserve("cloudflare", 100)  # ~4.1 Neurons
    ledger.record(CallUsage(provider="cloudflare", est_tokens=100, neurons=9.0))
    assert ledger.snapshot()["cloudflare"]["neurons_day"] == pytest.approx(9499.0)
    clock.now = utc("2026-11-20T00:00:01")
    assert ledger.snapshot()["cloudflare"]["neurons_day"] == 0.0
    assert ledger.reserve("cloudflare", 1000)


def test_cloudflare_reservation_counts_max_output() -> None:
    ledger = InMemoryLedger(cloudflare=CloudflareLimits(neurons_per_day=300.0), clock=Clock())
    # 1024 output tokens hold ~298 Neurons
    assert ledger.reserve("cloudflare", 10, max_output_tokens=1024)
    assert not ledger.reserve("cloudflare", 10, max_output_tokens=1024)


def test_cloudflare_neurons_estimated_without_usage_field() -> None:
    ledger = InMemoryLedger(clock=Clock())
    ledger.record(
        CallUsage(provider="cloudflare", est_tokens=0, prompt_tokens=28, completion_tokens=8)
    )
    # 28 in + 8 out cost 3.47 live; the table gives the same
    assert ledger.snapshot()["cloudflare"]["neurons_day"] == pytest.approx(3.47, abs=0.01)


# ---------------------------------------------------------------------------- Modal


def test_modal_cold_start_cost_and_cap() -> None:
    clock = Clock(utc("2026-11-19T10:00:00"))
    limits = ModalLimits()
    ledger = InMemoryLedger(modal=limits, clock=clock)
    rate = limits.gpu_usd_per_s * limits.overhead
    assert ledger.reserve("modal", 100)
    clock.now += 410
    ledger.record(CallUsage(provider="modal", est_tokens=100, latency_s=410.0))
    usd = ledger.snapshot()["modal"]["usd_month"]
    assert usd == pytest.approx((410 + 120) * rate)
    # a call while up only extends the tail
    clock.now += 10
    ledger.record(CallUsage(provider="modal", est_tokens=0, latency_s=1.0))
    assert ledger.snapshot()["modal"]["usd_month"] == pytest.approx((420 + 120) * rate)
    # after the tail the next call pays another cold start
    clock.now += 1000
    ledger.record(CallUsage(provider="modal", est_tokens=0, latency_s=0.5))
    assert ledger.snapshot()["modal"]["usd_month"] == pytest.approx((540 + 526) * rate)


def test_modal_month_cap_session_cap_and_month_reset() -> None:
    clock = Clock(utc("2026-11-30T23:00:00"))
    ledger = InMemoryLedger(modal=ModalLimits(usd_per_month=30.0), clock=clock)
    ledger.record(CallUsage(provider="modal", est_tokens=0, usd=29.9))
    assert not ledger.available("modal")  # a cold start costs ~$0.38
    clock.now = utc("2026-12-01T00:00:01")
    assert ledger.available("modal")
    session = InMemoryLedger(modal=ModalLimits(usd_per_session=1.0), clock=clock)
    session.record(CallUsage(provider="modal", est_tokens=0, usd=0.8))
    assert not session.available("modal")


def test_modal_in_flight_calls_hold_worst_case() -> None:
    ledger = InMemoryLedger(modal=ModalLimits(usd_per_month=0.9), clock=Clock())
    assert ledger.reserve("modal", 0)
    assert ledger.reserve("modal", 0)
    assert not ledger.reserve("modal", 0)  # 3 x $0.328 (526 s of L40S + 15 %) > $0.90
    ledger.record(CallUsage(provider="modal", est_tokens=0, sent=False))
    assert ledger.reserve("modal", 0)


# ---------------------------------------------------------------------------- threads


def test_ledger_thread_safety_smoke() -> None:
    ledger = InMemoryLedger(
        groq=GroqLimits(
            requests_per_day=1000,
            requests_per_minute=10_000,
            tokens_per_minute=10**9,
            tokens_per_day=10**9,
        ),
        clock=Clock(),
    )
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(200):
            ok = ledger.reserve("groq", 5)
            if ok:
                ledger.record(
                    CallUsage(provider="groq", est_tokens=5, prompt_tokens=3, completion_tokens=2)
                )
            ledger.record(CallUsage(provider="cloudflare", est_tokens=0, neurons=0.5))
            with lock:
                granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = ledger.snapshot()
    assert sum(granted) == 1000  # exactly the daily limit, never more
    assert snap["groq"]["requests_day"] == 1000
    assert snap["groq"]["tokens_day"] == 5000
    assert snap["groq"]["pending_requests"] == 0
    assert snap["cloudflare"]["neurons_day"] == pytest.approx(800.0)


# ---------------------------------------------------------------------------- breaker


def test_breaker_transitions() -> None:
    clock = Clock(0.0)
    b = CircuitBreaker("groq", failure_threshold=2, cooldown_s=10.0, clock=clock)
    assert state(b) == "closed"
    assert b.allow()
    b.record_failure()
    assert state(b) == "closed"
    b.record_failure()
    assert state(b) == "open"
    assert not b.allow()
    assert b.retry_after_s() == pytest.approx(10.0)
    clock.now = 10.0
    assert state(b) == "half_open"
    assert b.allow()  # the probe
    assert not b.allow()  # only one at a time
    b.record_failure()  # probe failed: open again
    assert state(b) == "open"
    clock.now = 20.0
    assert b.allow()
    b.record_success()
    assert state(b) == "closed"
    assert b.retry_after_s() is None


def test_breaker_success_resets_count_and_cancel_frees_probe() -> None:
    clock = Clock(0.0)
    b = CircuitBreaker("x", failure_threshold=2, cooldown_s=5.0, clock=clock)
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert state(b) == "closed"
    b.record_failure()
    clock.now = 5.0
    assert b.allow()
    b.cancel()
    assert b.allow()


def test_breaker_trip() -> None:
    clock = Clock(0.0)
    b = CircuitBreaker("x", clock=clock)
    b.trip(42.0)
    assert state(b) == "open"
    b.trip(1.0)  # a shorter trip does not shorten the open time
    clock.now = 41.0
    assert state(b) == "open"
    clock.now = 42.0
    assert state(b) == "half_open"


def test_breaker_rejects_bad_settings() -> None:
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker("x", failure_threshold=0)
    with pytest.raises(ValueError, match="cooldown"):
        CircuitBreaker("x", cooldown_s=-1)


def test_breaker_thread_safety_smoke() -> None:
    b = CircuitBreaker("x", failure_threshold=10**6, cooldown_s=0.0)

    def worker() -> None:
        for _ in range(500):
            if b.allow():
                b.record_failure()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert b._failures == 4000  # no update lost
