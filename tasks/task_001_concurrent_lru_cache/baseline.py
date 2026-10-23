"""
Buggy baseline - the shape of answer a hurried LLM tends to produce.

Defects (each is exercised by at least one FAIL_TO_PASS test):
* no locking, so concurrent callers corrupt recency order and exceed capacity;
* wall-clock ``time.time()`` instead of the monotonic clock;
* eviction is plain LRU: expired entries are not discarded first;
* ``size()`` counts expired entries; ``ttl_ms=0`` and negative TTLs are mishandled;
* re-``put`` without a TTL leaves the old deadline in place;
* falsy values are treated as cache misses.
"""

import time
from collections import OrderedDict
from typing import Any, Optional


class ConcurrentLRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self.cache: OrderedDict = OrderedDict()
        self.expirations: dict = {}

    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        if key in self.expirations and time.time() > self.expirations[key]:
            del self.cache[key]
            del self.expirations[key]
            return None
        self.cache.move_to_end(key)
        value = self.cache[key]
        return value if value else None

    def put(self, key: str, value: Any, ttl_ms: Optional[int] = None) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value

        if ttl_ms:
            self.expirations[key] = time.time() + (ttl_ms / 1000.0)

        if len(self.cache) > self.capacity:
            oldest, _ = self.cache.popitem(last=False)
            self.expirations.pop(oldest, None)

    def size(self) -> int:
        return len(self.cache)
