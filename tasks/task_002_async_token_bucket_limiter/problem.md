# Task 002: Async Token Bucket Rate Limiter with Monotonic Drift Compensation

## Problem Brief
Implement an `AsyncTokenBucket` rate limiter class in Python supporting asynchronous workflows (`asyncio`).

### Methods Required:
1. `__init__(capacity: int, refill_rate_per_sec: float)`: Initializes bucket with max `capacity` tokens, initially full.
2. `async acquire(tokens: int = 1, timeout: Optional[float] = None) -> bool`:
   Attempts to acquire `tokens`. If available, deducts and returns `True` immediately.
   If not available, waits up to `timeout` seconds for enough tokens to refill. Returns `False` if timeout expires before tokens become available.
3. `available_tokens() -> float`: Returns current tokens without acquiring.

### Critical Edge Cases:
* Must use monotonic time (`time.monotonic()`) to avoid NTP clock skew artifacts.
* Must handle bursts up to `capacity` without overflowing.
* Must support concurrent async tasks contending for tokens without race conditions.
