"""
Reference Gold-Standard Human Solution
Thread-safe LRU Cache with RLock, TTL expiration, and strict O(1) complexity.
"""

from collections import OrderedDict
import threading
import time
from typing import Any, Optional


class ConcurrentLRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self.cache: OrderedDict = OrderedDict()
        self.expirations: dict = {}
        self.lock = threading.RLock()

    def _purge_expired_locked(self, key: str) -> bool:
        if key in self.expirations and time.time() > self.expirations[key]:
            self.cache.pop(key, None)
            self.expirations.pop(key, None)
            return True
        return False

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key not in self.cache:
                return None
            if self._purge_expired_locked(key):
                return None
            self.cache.move_to_end(key)
            return self.cache[key]

    def put(self, key: str, value: Any, ttl_ms: Optional[int] = None) -> None:
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value

            if ttl_ms is not None:
                self.expirations[key] = time.time() + (ttl_ms / 1000.0)
            elif key in self.expirations:
                del self.expirations[key]

            # Enforce capacity
            while len(self.cache) > self.capacity:
                # Evict oldest item
                oldest_key, _ = self.cache.popitem(last=False)
                self.expirations.pop(oldest_key, None)

    def size(self) -> int:
        with self.lock:
            now = time.time()
            # Clean expired items
            expired = [k for k, exp in self.expirations.items() if now > exp]
            for k in expired:
                self.cache.pop(k, None)
                self.expirations.pop(k, None)
            return len(self.cache)
