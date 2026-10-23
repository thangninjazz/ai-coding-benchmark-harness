"""
Buggy Baseline Implementation
Common LLM failure mode: Naive implementation using OrderedDict without thread safety,
or partial locking leading to deadlocks / race conditions under concurrent put() calls.
"""

from collections import OrderedDict
import time
from typing import Any, Optional


class ConcurrentLRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self.cache: OrderedDict = OrderedDict()
        self.expirations: dict = {}

    def get(self, key: str) -> Optional[Any]:
        # Bug: Not thread-safe, no eviction on read expiration check
        if key not in self.cache:
            return None
        if key in self.expirations and time.time() > self.expirations[key]:
            del self.cache[key]
            del self.expirations[key]
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: str, value: Any, ttl_ms: Optional[int] = None) -> None:
        # Bug: Race condition under multi-threading; size can exceed capacity
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value

        if ttl_ms is not None:
            self.expirations[key] = time.time() + (ttl_ms / 1000.0)

        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def size(self) -> int:
        return len(self.cache)
