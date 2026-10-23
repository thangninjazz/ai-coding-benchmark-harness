"""
Reference Gold-Standard Human Solution
Asynchronous Token Bucket with asyncio.Lock, monotonic timing, and non-blocking wait.
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
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        if tokens <= 0:
            raise ValueError("Requested tokens must be positive")
        if tokens > self.capacity:
            return False

        start_time = time.monotonic()

        while True:
            async with self.lock:
                self._refill_locked()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

                needed = tokens - self.tokens
                wait_seconds = needed / self.refill_rate

            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed + wait_seconds > timeout:
                    # Not enough time remaining to acquire
                    if elapsed < timeout:
                        await asyncio.sleep(timeout - elapsed)
                    return False

            await asyncio.sleep(min(wait_seconds, 0.05))

    def available_tokens(self) -> float:
        now = time.monotonic()
        elapsed = now - self.last_refill
        return min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
