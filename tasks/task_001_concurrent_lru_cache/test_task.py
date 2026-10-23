"""Tests for Task 001. Time is driven by a fake ``time.monotonic`` so TTL behaviour is
exact and nothing sleeps; ``time.time`` is sabotaged to prove the wall clock is unused."""

import sys
import threading
import time

import pytest

from implementation import ConcurrentLRUCache


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake)
    # Any use of the wall clock is a bug: make it nonsense that also jumps around.
    jumps = iter(range(10**9))
    monkeypatch.setattr(time, "time", lambda: 5e9 - next(jumps) * 1e6)
    return fake


# --------------------------------------------------------------------- plain LRU
def test_lru_eviction_order():
    cache = ConcurrentLRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_get_refreshes_recency():
    cache = ConcurrentLRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_put_update_replaces_value_and_refreshes_recency():
    cache = ConcurrentLRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 10
    assert cache.size() == 2


def test_capacity_one():
    cache = ConcurrentLRUCache(capacity=1)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.size() == 1


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_invalid_capacity_rejected(bad):
    with pytest.raises(ValueError):
        ConcurrentLRUCache(capacity=bad)


def test_falsy_values_roundtrip():
    cache = ConcurrentLRUCache(capacity=8)
    falsy = {"zero": 0, "empty": "", "false": False, "list": [], "dict": {}}
    for key, value in falsy.items():
        cache.put(key, value)
    for key, value in falsy.items():
        assert cache.get(key) == value
        assert cache.get(key) is not None or value is None


# --------------------------------------------------------------------------- TTL
def test_ttl_valid_before_deadline_and_expired_at_deadline(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("k", "v", ttl_ms=100)
    clock.advance_ms(99)
    assert cache.get("k") == "v"
    clock.advance_ms(1)  # exactly at the deadline
    assert cache.get("k") is None


def test_ttl_zero_is_immediately_expired(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("k", "v", ttl_ms=0)
    assert cache.get("k") is None
    assert cache.size() == 0


def test_negative_ttl_rejected(clock):
    cache = ConcurrentLRUCache(capacity=4)
    with pytest.raises(ValueError):
        cache.put("k", "v", ttl_ms=-1)
    assert cache.size() == 0


def test_put_without_ttl_clears_previous_ttl(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("k", "old", ttl_ms=50)
    cache.put("k", "new")
    clock.advance_ms(10_000)
    assert cache.get("k") == "new"
    assert cache.size() == 1


def test_put_with_ttl_restarts_deadline(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("k", 1, ttl_ms=100)
    clock.advance_ms(80)
    cache.put("k", 2, ttl_ms=100)
    clock.advance_ms(80)  # 160 ms after the first put, 80 ms after the second
    assert cache.get("k") == 2
    clock.advance_ms(20)
    assert cache.get("k") is None


def test_ttl_applies_per_key(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("short", 1, ttl_ms=10)
    cache.put("long", 2, ttl_ms=1000)
    cache.put("forever", 3)
    clock.advance_ms(500)
    assert cache.get("short") is None
    assert cache.get("long") == 2
    assert cache.get("forever") == 3


def test_size_counts_only_valid_entries(clock):
    cache = ConcurrentLRUCache(capacity=10)
    cache.put("a", 1, ttl_ms=10)
    cache.put("b", 2, ttl_ms=10)
    cache.put("c", 3)
    assert cache.size() == 3
    clock.advance_ms(10)
    assert cache.size() == 1


def test_get_on_expired_entry_removes_it(clock):
    cache = ConcurrentLRUCache(capacity=2)
    cache.put("a", 1, ttl_ms=10)
    cache.put("b", 2)
    clock.advance_ms(10)
    assert cache.get("a") is None
    cache.put("c", 3)  # fits because "a" is gone: nothing live may be evicted
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_expired_entries_are_evicted_before_lru(clock):
    cache = ConcurrentLRUCache(capacity=3)
    cache.put("old1", 1)
    cache.put("old2", 2)
    cache.put("stale", 3, ttl_ms=100)  # most recently used, but about to expire
    clock.advance_ms(200)
    cache.put("new", 4)
    # Plain LRU would evict "old1"; the spec says the expired entry must go first.
    assert cache.get("old1") == 1
    assert cache.get("old2") == 2
    assert cache.get("stale") is None
    assert cache.get("new") == 4


def test_all_expired_entries_are_purged_together_before_any_live_eviction(clock):
    cache = ConcurrentLRUCache(capacity=4)
    cache.put("live", "L")
    cache.put("x1", 1, ttl_ms=10)
    cache.put("x2", 2, ttl_ms=20)
    cache.put("x3", 3, ttl_ms=30)
    clock.advance_ms(100)
    cache.put("n1", 4)
    cache.put("n2", 5)
    cache.put("n3", 6)
    assert cache.get("live") == "L"
    assert [cache.get(k) for k in ("n1", "n2", "n3")] == [4, 5, 6]


def test_repeated_ttl_updates_do_not_resurrect_or_leak(clock):
    cache = ConcurrentLRUCache(capacity=3)
    for i in range(500):
        cache.put("hot", i, ttl_ms=50)
        clock.advance_ms(10)
    assert cache.get("hot") == 499
    clock.advance_ms(50)
    assert cache.get("hot") is None
    assert cache.size() == 0


def test_operations_scale(clock):
    n = 20_000
    cache = ConcurrentLRUCache(capacity=n)
    start = time.perf_counter()
    for i in range(n):
        cache.put(f"k{i}", i, ttl_ms=1_000_000)
        cache.get(f"k{i}")
        assert cache.size() == i + 1
    # O(n) work per call would need ~2e8 steps here; amortized O(log n) takes well under 1s.
    assert time.perf_counter() - start < 8.0


# ------------------------------------------------------------------ concurrency
@pytest.fixture
def aggressive_switching():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def _run_threads(targets):
    errors = []

    def wrap(fn):
        def inner():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - report anything a worker raises
                errors.append(repr(exc))
        return inner

    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker threads deadlocked"
    return errors


def test_concurrent_integrity(aggressive_switching):
    capacity, workers, ops = 8, 12, 4_000
    cache = ConcurrentLRUCache(capacity=capacity)
    barrier = threading.Barrier(workers + 1)
    stop = threading.Event()
    oversize = []

    def worker(tid):
        def run():
            barrier.wait()
            for i in range(ops):
                key = f"k{(tid * 7 + i) % 40}"  # heavily shared key space
                cache.put(key, key, ttl_ms=1 if (tid + i) % 3 == 0 else None)
                got = cache.get(key)
                assert got is None or got == key, f"{key!r} returned {got!r}"
                if i % 25 == 0:
                    cache.size()
        return run

    def monitor():
        barrier.wait()
        while not stop.is_set():
            observed = cache.size()
            if observed > capacity:
                oversize.append(observed)

    mon = threading.Thread(target=monitor)
    mon.start()
    errors = _run_threads([worker(t) for t in range(workers)])
    stop.set()
    mon.join(timeout=10)

    assert errors == []
    assert oversize == [], f"size() exceeded capacity: {oversize[:5]}"
    assert cache.size() <= capacity


def test_concurrent_no_lost_updates(aggressive_switching):
    workers, keys_per_worker, rounds = 8, 20, 300
    cache = ConcurrentLRUCache(capacity=workers * keys_per_worker)

    def worker(tid):
        def run():
            for r in range(rounds):
                for k in range(keys_per_worker):
                    cache.put(f"{tid}:{k}", (tid, k, r))
            for k in range(keys_per_worker):
                assert cache.get(f"{tid}:{k}") == (tid, k, rounds - 1)
        return run

    assert _run_threads([worker(t) for t in range(workers)]) == []
    assert cache.size() == workers * keys_per_worker


def test_concurrent_readers_against_evicting_writers(aggressive_switching):
    """Readers race keys that writers are evicting at full speed (tiny capacity)."""
    capacity, writers, readers, ops = 4, 4, 8, 6_000
    cache = ConcurrentLRUCache(capacity=capacity)
    barrier = threading.Barrier(writers + readers)
    progress = [0] * writers

    def writer(tid):
        def run():
            barrier.wait()
            for i in range(ops):
                progress[tid] = i
                key = f"w{tid}:{i}"
                cache.put(key, key, ttl_ms=5 if i % 2 else None)
        return run

    def reader(rid):
        def run():
            barrier.wait()
            state = rid + 1
            for _ in range(ops * 3):
                state = (state * 1103515245 + 12345) % 2**31
                tid = state % writers
                key = f"w{tid}:{max(0, progress[tid] - (state >> 8) % 6)}"
                got = cache.get(key)
                assert got is None or got == key, f"{key!r} returned {got!r}"
        return run

    errors = _run_threads([writer(t) for t in range(writers)] + [reader(r) for r in range(readers)])
    assert errors == []
    assert cache.size() <= capacity
