"""The per-client token bucket (agent C1)."""

from __future__ import annotations

import threading

import pytest

from chapterverse.ratelimit import Decision, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_refill() -> None:
    clock = Clock()
    bucket = TokenBucket(per_minute=6, burst=2, clock=clock)
    assert bucket.take("a").allowed
    assert bucket.take("a").allowed
    refused = bucket.take("a")
    assert not refused.allowed
    assert refused.retry_after_s == pytest.approx(10.0)
    assert refused.retry_after_header == "10"
    assert bucket.take("b").allowed  # keys are independent
    clock.now = 10.0
    assert bucket.take("a").allowed
    assert not bucket.take("a").allowed
    clock.now = 1000.0
    assert bucket.take("a").allowed
    assert bucket.take("a").allowed
    assert not bucket.take("a").allowed


def test_retry_after_header_is_at_least_one_second() -> None:
    assert Decision(allowed=False, retry_after_s=0.01).retry_after_header == "1"
    assert Decision(allowed=False, retry_after_s=2.2).retry_after_header == "3"


def test_table_stays_bounded() -> None:
    clock = Clock()
    bucket = TokenBucket(per_minute=60, burst=1, max_keys=10, clock=clock)
    for i in range(10):
        bucket.take(f"k{i}")
    clock.now = 5.0  # every bucket has refilled
    bucket.take("new")
    assert len(bucket) <= 10
    for i in range(100):
        bucket.take(f"busy{i}")
    assert len(bucket) <= 11


def test_bad_settings() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(per_minute=0)
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(burst=0)


def test_thread_safe() -> None:
    bucket = TokenBucket(per_minute=0.0001, burst=50, clock=Clock())
    allowed: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(20):
            ok = bucket.take("shared").allowed
            with lock:
                allowed.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(allowed) == 50
    assert "TokenBucket" in repr(bucket)
