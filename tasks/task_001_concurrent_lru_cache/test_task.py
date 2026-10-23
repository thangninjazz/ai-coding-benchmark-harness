import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from implementation import ConcurrentLRUCache


def test_basic_lru_eviction():
    cache = ConcurrentLRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)  # Evicts "b" because "a" was recently accessed
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_ttl_expiration():
    cache = ConcurrentLRUCache(capacity=5)
    cache.put("temp", "val", ttl_ms=50)
    assert cache.get("temp") == "val"
    time.sleep(0.08)
    assert cache.get("temp") is None
    assert cache.size() == 0


def test_concurrent_integrity():
    cache = ConcurrentLRUCache(capacity=10)
    num_threads = 16
    ops_per_thread = 200

    def worker(thread_id: int):
        for i in range(ops_per_thread):
            key = f"key_{thread_id}_{i % 25}"
            cache.put(key, i)
            _ = cache.get(key)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, t) for t in range(num_threads)]
        for f in futures:
            f.result()

    # Invariants under concurrency:
    # 1. Size must NEVER exceed configured capacity
    assert cache.size() <= 10
