import asyncio
import pytest

from implementation import AsyncTokenBucket


@pytest.mark.asyncio
async def test_immediate_burst_capacity():
    bucket = AsyncTokenBucket(capacity=5, refill_rate_per_sec=10.0)
    assert bucket.available_tokens() == pytest.approx(5.0, abs=0.1)

    # Acquire all 5 immediately
    for _ in range(5):
        assert await bucket.acquire(1) is True

    # 6th should fail without wait
    assert await bucket.acquire(1, timeout=0.01) is False


@pytest.mark.asyncio
async def test_timed_refill_acquisition():
    # 10 tokens/sec -> 0.1s per token
    bucket = AsyncTokenBucket(capacity=2, refill_rate_per_sec=10.0)
    assert await bucket.acquire(2) is True

    # Next acquire with sufficient timeout succeeds
    success = await bucket.acquire(1, timeout=0.25)
    assert success is True


@pytest.mark.asyncio
async def test_concurrent_async_contention():
    bucket = AsyncTokenBucket(capacity=10, refill_rate_per_sec=50.0)

    async def requester(n: int):
        return await bucket.acquire(1, timeout=0.5)

    # 25 tasks concurrently competing for tokens
    tasks = [requester(i) for i in range(25)]
    results = await asyncio.gather(*tasks)

    # All 25 should eventually succeed within timeout given 50 tokens/sec
    assert sum(results) >= 20
