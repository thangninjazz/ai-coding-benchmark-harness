"""Tests for Task 002.

Real time is used (the spec fixes the clock to ``time.monotonic``), so every timing
assertion is a *bound derived from measured timestamps* rather than a fixed constant:
upper bounds come from "tokens cannot accrue faster than the rate", lower bounds from
"at least this much real time passed". That keeps the suite exact on a loaded CI box.
"""

import asyncio
import time

import pytest

from implementation import AsyncTokenBucket

EPS = 1e-6

pytestmark = pytest.mark.asyncio


def guard(coro, seconds: float = 5.0):
    """Fail (instead of hanging) if an acquire never returns."""
    return asyncio.wait_for(coro, timeout=seconds)


# ------------------------------------------------------------------ construction
@pytest.mark.parametrize("capacity,rate", [(0, 1.0), (-1, 1.0), (1, 0), (1, -2.0)])
async def test_invalid_construction_rejected(capacity, rate):
    with pytest.raises(ValueError):
        AsyncTokenBucket(capacity=capacity, refill_rate_per_sec=rate)


async def test_starts_full():
    bucket = AsyncTokenBucket(capacity=7, refill_rate_per_sec=1.0)
    assert bucket.available_tokens() == pytest.approx(7.0)


@pytest.mark.parametrize("n", [0, -1, -5])
async def test_nonpositive_request_rejected(n):
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=1.0)
    with pytest.raises(ValueError):
        await guard(bucket.acquire(n))


async def test_request_larger_than_capacity_returns_false_immediately():
    bucket = AsyncTokenBucket(capacity=3, refill_rate_per_sec=1000.0)
    start = time.monotonic()
    assert await guard(bucket.acquire(4), 2.0) is False  # timeout=None must not wait
    assert await guard(bucket.acquire(4, timeout=5.0), 2.0) is False
    assert time.monotonic() - start < 1.0
    assert bucket.available_tokens() == pytest.approx(3.0, abs=0.5)


# ------------------------------------------------------------------ burst & refill
async def test_burst_up_to_capacity_then_reject():
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=0.5)  # 2 s per token
    for _ in range(5):
        assert await guard(bucket.acquire(1)) is True
    assert await guard(bucket.acquire(1, timeout=0)) is False


async def test_multi_token_acquire():
    bucket = AsyncTokenBucket(capacity=10, refill_rate_per_sec=0.01)
    assert await guard(bucket.acquire(7)) is True
    assert await guard(bucket.acquire(4, timeout=0)) is False  # only 3 left
    assert await guard(bucket.acquire(3, timeout=0)) is True


async def test_refill_rate_is_exact():
    rate = 50.0
    bucket = AsyncTokenBucket(capacity=100, refill_rate_per_sec=rate)
    before = time.monotonic()
    assert await guard(bucket.acquire(100)) is True  # drain
    t0 = time.monotonic()
    await asyncio.sleep(0.2)
    t1 = time.monotonic()
    available = bucket.available_tokens()
    after = time.monotonic()
    assert (t1 - t0) * rate - EPS <= available <= (after - before) * rate + EPS


async def test_refill_is_capped_at_capacity():
    rate = 20.0
    bucket = AsyncTokenBucket(capacity=3, refill_rate_per_sec=rate)
    assert await guard(bucket.acquire(3)) is True
    await asyncio.sleep(0.3)  # would accrue 6 tokens uncapped
    start = time.monotonic()
    assert bucket.available_tokens() <= 3.0 + EPS
    granted = sum([await guard(bucket.acquire(1, timeout=0)) for _ in range(10)])
    budget = 3 + rate * (time.monotonic() - start)
    assert 3 <= granted <= budget + EPS


async def test_waiting_acquire_succeeds_after_refill():
    bucket = AsyncTokenBucket(capacity=2, refill_rate_per_sec=10.0)  # 0.1 s per token
    assert await guard(bucket.acquire(2)) is True
    start = time.monotonic()
    assert await guard(bucket.acquire(1, timeout=3.0)) is True
    waited = time.monotonic() - start
    assert waited >= 0.08  # one token cannot appear faster than 0.1 s (minus jitter)


async def test_timeout_none_waits_as_long_as_needed():
    bucket = AsyncTokenBucket(capacity=3, refill_rate_per_sec=100.0)
    assert await guard(bucket.acquire(3)) is True
    assert await guard(bucket.acquire(3, timeout=None)) is True  # ~30 ms


async def test_sequential_acquires_respect_the_rate():
    rate = 100.0
    bucket = AsyncTokenBucket(capacity=10, refill_rate_per_sec=rate)
    assert await guard(bucket.acquire(10)) is True  # drain
    t0 = time.monotonic()
    tokens_at_t0 = bucket.available_tokens()
    for _ in range(10):
        assert await guard(bucket.acquire(1, timeout=5.0)) is True
    elapsed = time.monotonic() - t0
    # Ten tokens were spent; they can only have come from the stock at t0 plus accrual.
    assert tokens_at_t0 + rate * elapsed + EPS >= 10


# ---------------------------------------------------------------------- timeouts
async def test_timeout_gives_up_and_returns_false():
    bucket = AsyncTokenBucket(capacity=1, refill_rate_per_sec=0.5)  # 2 s per token
    assert await guard(bucket.acquire(1)) is True
    start = time.monotonic()
    assert await guard(bucket.acquire(1, timeout=0.1), 3.0) is False
    assert time.monotonic() - start < 1.5  # honoured, not 2 s


async def test_failed_acquire_does_not_consume_tokens():
    rate = 1.0
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=rate)
    before = time.monotonic()
    assert await guard(bucket.acquire(4)) is True  # 1 token left
    assert await guard(bucket.acquire(3, timeout=0.05)) is False
    available = bucket.available_tokens()
    elapsed = time.monotonic() - before
    assert 1.0 - EPS <= available <= 1.0 + rate * elapsed + EPS
    assert await guard(bucket.acquire(1, timeout=0)) is True


async def test_timeout_is_honoured_while_another_waiter_is_blocked():
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=2.0)
    assert await guard(bucket.acquire(5)) is True
    slow = asyncio.create_task(bucket.acquire(5))  # needs ~2.5 s
    await asyncio.sleep(0.05)
    start = time.monotonic()
    assert await guard(bucket.acquire(1, timeout=0.05), 3.0) is False
    assert time.monotonic() - start < 1.0
    slow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow


# ------------------------------------------------------------------- monotonic clock
async def test_wall_clock_jumping_forward_grants_nothing(monkeypatch):
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=0.1)
    assert await guard(bucket.acquire(5)) is True
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 1e7)
    assert bucket.available_tokens() < 1.0
    assert await guard(bucket.acquire(1, timeout=0)) is False


async def test_wall_clock_jumping_backward_does_not_stall_refill(monkeypatch):
    rate = 50.0
    bucket = AsyncTokenBucket(capacity=4, refill_rate_per_sec=rate)
    assert await guard(bucket.acquire(4)) is True
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 1e6)
    t0 = time.monotonic()
    await asyncio.sleep(0.15)
    t1 = time.monotonic()
    assert bucket.available_tokens() >= min(4.0, (t1 - t0) * rate) - EPS
    assert await guard(bucket.acquire(1, timeout=2.0)) is True


# -------------------------------------------------------------------- concurrency
async def test_concurrent_burst_never_over_grants():
    bucket = AsyncTokenBucket(capacity=10, refill_rate_per_sec=0.01)
    results = await guard(asyncio.gather(*[bucket.acquire(1, timeout=0) for _ in range(60)]))
    assert sum(results) == 10
    assert bucket.available_tokens() >= 0.0


async def test_concurrent_grants_conserve_tokens_over_time():
    capacity, rate = 5, 40.0
    before = time.monotonic()
    bucket = AsyncTokenBucket(capacity=capacity, refill_rate_per_sec=rate)
    lowest = [capacity]
    stop = asyncio.Event()

    async def watch():
        while not stop.is_set():
            lowest[0] = min(lowest[0], bucket.available_tokens())
            await asyncio.sleep(0)

    watcher = asyncio.create_task(watch())
    results = await guard(
        asyncio.gather(*[bucket.acquire(1, timeout=0.5) for _ in range(80)])
    )
    end = time.monotonic()
    stop.set()
    await watcher
    granted = sum(results)
    assert capacity <= granted <= capacity + rate * (end - before) + EPS
    assert lowest[0] >= -EPS


async def test_cancelled_waiter_leaves_bucket_usable():
    bucket = AsyncTokenBucket(capacity=1, refill_rate_per_sec=10.0)
    assert await guard(bucket.acquire(1)) is True
    waiter = asyncio.create_task(bucket.acquire(1))
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert await guard(bucket.acquire(1, timeout=2.0)) is True
    assert 0.0 <= bucket.available_tokens() <= 1.0 + EPS


async def test_waiting_does_not_block_the_event_loop():
    bucket = AsyncTokenBucket(capacity=1, refill_rate_per_sec=5.0)  # 0.2 s per token
    assert await guard(bucket.acquire(1)) is True
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        assert await guard(bucket.acquire(1, timeout=3.0)) is True
    finally:
        task.cancel()
    assert ticks >= 5  # a blocking sleep would starve the ticker
