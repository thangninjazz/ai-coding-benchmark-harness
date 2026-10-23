"""
Buggy Baseline Implementation
Common LLM failure mode: Uses time.time() instead of monotonic, lacks asyncio.Lock,
or busy-waits with time.sleep blocking the event loop.
"""

import asyncio
import time
from typing import Optional


class AsyncTokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        if capacity <= 0 or refill_rate_per_sec <= 0:
            raise ValueError("Parameters must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens = float(capacity)
        self.last_refill = time.time()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        # Bug: Not async-safe; tokens can drop below zero under concurrent coroutines
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def available_tokens(self) -> float:
        self._refill()
        return self.tokens
