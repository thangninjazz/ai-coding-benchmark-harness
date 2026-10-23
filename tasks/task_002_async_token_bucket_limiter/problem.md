# Task 002: Async Token Bucket Rate Limiter with Monotonic Drift Compensation

Implement `AsyncTokenBucket` in a single module (`implementation.py`) for use from
`asyncio` code.

## Interface

```python
class AsyncTokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float): ...
    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool: ...
    def available_tokens(self) -> float: ...
```

## Specification

**Construction**
* The bucket starts **full** (`capacity` tokens).
* `capacity <= 0` or `refill_rate_per_sec <= 0` raises `ValueError`.

**Refill**
* Tokens accrue continuously at `refill_rate_per_sec` and are capped at `capacity`
  (no overflow, however long the bucket sits idle).
* Elapsed time must be measured with `time.monotonic()` only, looked up on the `time`
  module at the moment it is needed. The wall clock (`time.time()`) can jump forwards
  or backwards (NTP step, DST, manual change) and must have **no** effect on refill.
* Waiting must compensate for drift: after every sleep, re-measure and recompute the
  remaining wait from the current token count. Never assume a sleep lasted exactly the
  requested time, and never count a token that has not accrued yet.

**`acquire(tokens=1, timeout=None)`**
* `tokens <= 0` raises `ValueError`.
* `tokens > capacity` can never be satisfied: return `False` immediately (even when
  `timeout is None`; do not wait).
* If enough tokens are available, deduct them and return `True` without suspending
  indefinitely.
* Otherwise wait for the tokens to accrue. `timeout=None` waits as long as it takes;
  `timeout=t` gives up after at most about `t` seconds and returns `False`.
* A call that returns `False` (or is cancelled) must not consume any tokens.
* Waiting must not block the event loop.

**`available_tokens()`**
* Synchronous. Returns the current token count (a float in `[0, capacity]`) including
  refill up to now, without consuming anything.

## Concurrency
Many coroutines may call `acquire` at once. The total number of tokens granted can never
exceed `capacity + refill_rate * elapsed`, and `available_tokens()` is never negative.
Cancelling a waiting `acquire` must leave the bucket fully usable.

Standard library only.
