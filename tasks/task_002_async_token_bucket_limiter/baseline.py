"""
Buggy baseline - the shape of answer a hurried LLM tends to produce.

Defects (each is exercised by at least one FAIL_TO_PASS test):
* wall clock ``time.time()`` instead of ``time.monotonic()``;
* ``acquire`` never waits: it ignores ``timeout`` and returns False at once;
* no validation of ``tokens`` (zero/negative/oversized requests);
* ``available_tokens`` mutates state and can go negative after a backwards clock jump.
"""

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
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def available_tokens(self) -> float:
        self._refill()
        return self.tokens
