"""The per-client token bucket (agent C1)."""

from __future__ import annotations

import threading
from datetime import date

import pytest

from chapterverse.contracts import sha256_hex
from chapterverse.ratelimit import (
    DEFAULT_DAILY_ASK_CAP,
    DEFAULT_GLOBAL_DAILY_ANSWERS,
    DEFAULT_HOSTING_SHARE,
    DailyBudgetCounts,
    Decision,
    InMemoryDailyBudget,
    InMemoryDailyCap,
    TokenBucket,
    client_key_hash,
)


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


# ---------------------------------------------------------------------------- daily cap


def test_client_key_hash_never_stores_ip() -> None:
    ip = "198.51.100.42"
    salt = "secret-salt-xyz"
    key = client_key_hash(ip, salt)
    assert len(key) == 64
    assert key == sha256_hex(salt + ip)
    assert ip not in key
    assert salt not in key
    # Salt changes the digest
    other_salt = client_key_hash(ip, "different-salt")
    assert other_salt != key
    assert len(other_salt) == 64
    # Different IPs have different hashes
    other_ip = client_key_hash("198.51.100.43", salt)
    assert other_ip != key
    # Empty salt defaults cleanly
    no_salt = client_key_hash(ip)
    assert no_salt == sha256_hex(ip)


def test_in_memory_daily_cap_under_at_and_over() -> None:
    cap_store = InMemoryDailyCap()
    today = date(2026, 11, 20)
    key_a = client_key_hash("10.0.0.1", "salt")
    key_b = client_key_hash("10.0.0.2", "salt")
    cap = 3

    # Initially under the cap
    assert cap_store.get(key_a, today) == 0
    assert cap_store.get(key_a, today) < cap

    # 1st answer: under the cap
    assert cap_store.increment(key_a, today) == 1
    assert cap_store.get(key_a, today) == 1
    assert cap_store.get(key_a, today) < cap

    # 2nd answer: under the cap
    assert cap_store.increment(key_a, today) == 2
    assert cap_store.get(key_a, today) < cap

    # 3rd answer: at the cap
    assert cap_store.increment(key_a, today) == 3
    assert cap_store.get(key_a, today) == cap
    assert cap_store.get(key_a, today) >= cap  # at cap

    # Over the cap
    assert cap_store.increment(key_a, today) == 4
    assert cap_store.get(key_a, today) > cap

    # Independent client is unaffected
    assert cap_store.get(key_b, today) == 0
    assert cap_store.get(key_b, today) < cap

    # Tomorrow is unaffected
    tomorrow = date(2026, 11, 21)
    assert cap_store.get(key_a, tomorrow) == 0

    assert DEFAULT_DAILY_ASK_CAP == 40


def test_in_memory_daily_cap_pruning() -> None:
    cap_store = InMemoryDailyCap()
    key = client_key_hash("10.0.0.1", "salt")
    d0 = date(2026, 11, 1)
    d1 = date(2026, 11, 2)
    d2 = date(2026, 11, 3)
    d4 = date(2026, 11, 5)  # 4 days after d0 (> 2 days)

    cap_store.increment(key, d0)
    cap_store.increment(key, d1)
    cap_store.increment(key, d2)
    assert len(cap_store) == 3

    # Incrementing on d4 prunes entries older than 2 days (d0 and d1 are > 2 days before d4)
    cap_store.increment(key, d4)
    assert cap_store.get(key, d0) == 0
    assert cap_store.get(key, d1) == 0
    assert cap_store.get(key, d2) == 1
    assert cap_store.get(key, d4) == 1
    assert len(cap_store) == 2


def test_in_memory_daily_cap_thread_safe() -> None:
    cap_store = InMemoryDailyCap()
    today = date(2026, 11, 20)
    key = client_key_hash("10.0.0.1", "salt")

    def worker() -> None:
        for _ in range(25):
            cap_store.increment(key, today)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cap_store.get(key, today) == 100
    assert "InMemoryDailyCap" in repr(cap_store)


def test_in_memory_daily_budget_under_and_over() -> None:
    budget = InMemoryDailyBudget()
    today = date(2026, 11, 20)
    tomorrow = date(2026, 11, 21)

    assert budget.get(today) == DailyBudgetCounts(0, 0)

    # 1. Non-hosting request
    c1 = budget.increment(today, hosting=False)
    assert c1 == DailyBudgetCounts(1, 0)
    assert budget.get(today) == DailyBudgetCounts(1, 0)

    # 2. Hosting request
    c2 = budget.increment(today, hosting=True)
    assert c2 == DailyBudgetCounts(2, 1)
    assert budget.get(today) == DailyBudgetCounts(2, 1)

    # 3. Another non-hosting
    c3 = budget.increment(today, hosting=False)
    assert c3 == DailyBudgetCounts(3, 1)
    assert budget.get(today) == DailyBudgetCounts(3, 1)

    # Tomorrow is unaffected
    assert budget.get(tomorrow) == DailyBudgetCounts(0, 0)

    assert DEFAULT_GLOBAL_DAILY_ANSWERS == 400
    assert DEFAULT_HOSTING_SHARE == 0.2


def test_in_memory_daily_budget_pruning() -> None:
    budget = InMemoryDailyBudget()
    d0 = date(2026, 11, 1)
    d1 = date(2026, 11, 2)
    d2 = date(2026, 11, 3)
    d4 = date(2026, 11, 5)  # > 2 days after d0 and d1

    budget.increment(d0, hosting=True)
    budget.increment(d1, hosting=False)
    budget.increment(d2, hosting=True)
    assert len(budget) == 3

    budget.increment(d4, hosting=False)
    assert budget.get(d0) == DailyBudgetCounts(0, 0)
    assert budget.get(d1) == DailyBudgetCounts(0, 0)
    assert budget.get(d2) == DailyBudgetCounts(1, 1)
    assert budget.get(d4) == DailyBudgetCounts(1, 0)
    assert len(budget) == 2


def test_in_memory_daily_budget_thread_safe() -> None:
    budget = InMemoryDailyBudget()
    today = date(2026, 11, 20)

    def worker_hosting() -> None:
        for _ in range(25):
            budget.increment(today, hosting=True)

    def worker_other() -> None:
        for _ in range(25):
            budget.increment(today, hosting=False)

    threads = [
        threading.Thread(target=worker_hosting),
        threading.Thread(target=worker_hosting),
        threading.Thread(target=worker_other),
        threading.Thread(target=worker_other),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    res = budget.get(today)
    assert res.total == 100
    assert res.hosting == 50
    assert "InMemoryDailyBudget" in repr(budget)
