"""
Reference solution.

Tokens are refilled lazily from ``time.monotonic()`` deltas. The check-and-deduct step
runs under an ``asyncio.Lock`` so it stays atomic even if future edits add an ``await``
inside it. A waiter never trusts how long it slept: after each wake-up it refills again
and recomputes the remaining wait from the live token count (drift compensation).
"""

import asyncio
import time
from typing import Optional

_MIN_SLEEP = 0.001


class AsyncTokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        if capacity <= 0 or refill_rate_per_sec <= 0:
            raise ValueError("capacity and refill_rate_per_sec must be positive")
        self._capacity = capacity
        self._rate = float(refill_rate_per_sec)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(float(self._capacity), self._tokens + elapsed * self._rate)
            self._last = now

    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self._capacity:
            return False

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self._rate

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if wait > remaining:
                    # Cannot be satisfied in time: spend the budget, then give up.
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    return False
            await asyncio.sleep(max(wait, _MIN_SLEEP))

    def available_tokens(self) -> float:
        elapsed = max(0.0, time.monotonic() - self._last)
        return min(float(self._capacity), self._tokens + elapsed * self._rate)
