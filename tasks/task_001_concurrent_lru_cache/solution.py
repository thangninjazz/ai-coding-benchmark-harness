"""
Reference solution.

An ``OrderedDict`` tracks recency (O(1) get/put). A lazily-invalidated min-heap keyed by
expiry time lets ``put`` and ``size`` discard expired entries in amortized O(log n)
without scanning the cache. Every public method runs under one re-entrant lock.
"""

import heapq
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


class ConcurrentLRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self._capacity = capacity
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, Any] = OrderedDict()
        # key -> (deadline, seq) of the *current* TTL record for that key
        self._expiry: Dict[str, Tuple[float, int]] = {}
        # (deadline, seq, key); records that no longer match ``_expiry`` are stale
        self._heap: List[Tuple[float, int, str]] = []
        self._seq = 0

    def _drop_expired(self, now: float) -> None:
        while self._heap and self._heap[0][0] <= now:
            deadline, seq, key = heapq.heappop(self._heap)
            if self._expiry.get(key) == (deadline, seq):
                del self._expiry[key]
                del self._entries[key]

    def _compact_heap(self) -> None:
        if len(self._heap) > 2 * len(self._expiry) + 32:
            self._heap = [(d, s, k) for k, (d, s) in self._expiry.items()]
            heapq.heapify(self._heap)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._entries:
                return None
            record = self._expiry.get(key)
            if record is not None and time.monotonic() >= record[0]:
                del self._expiry[key]
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return self._entries[key]

    def put(self, key: str, value: Any, ttl_ms: Optional[int] = None) -> None:
        if ttl_ms is not None and ttl_ms < 0:
            raise ValueError("ttl_ms must be non-negative")
        with self._lock:
            now = time.monotonic()
            self._entries[key] = value
            self._entries.move_to_end(key)
            if ttl_ms is None:
                self._expiry.pop(key, None)
            else:
                self._seq += 1
                deadline = now + ttl_ms / 1000.0
                self._expiry[key] = (deadline, self._seq)
                heapq.heappush(self._heap, (deadline, self._seq, key))

            if len(self._entries) > self._capacity:
                self._drop_expired(now)
                while len(self._entries) > self._capacity:
                    victim, _ = self._entries.popitem(last=False)
                    self._expiry.pop(victim, None)
            self._compact_heap()

    def size(self) -> int:
        with self._lock:
            self._drop_expired(time.monotonic())
            return len(self._entries)
