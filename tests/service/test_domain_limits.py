"""DomainLimiter tests: intervals, concurrency caps, queue time, overrides."""

import asyncio

import pytest

from web_extractor.service.domain_limits import DomainLimiter, HostLimit


class RecordingSleeper:
    """Sleeper that records requested waits without actually sleeping."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.mark.asyncio
async def test_min_interval_waits_between_starts_on_same_host() -> None:
    sleeper = RecordingSleeper()
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=4, min_interval_s=5.0), sleeper=sleeper
    )
    first = await limiter.acquire("site.test")
    assert sleeper.sleeps == []  # first request starts immediately
    second = await limiter.acquire("site.test")
    assert len(sleeper.sleeps) == 1
    # No real time passes between the two starts, so the full interval is due.
    assert 4.0 < sleeper.sleeps[0] <= 5.0
    assert second.queue_ms >= 0
    first.release()
    second.release()


@pytest.mark.asyncio
async def test_different_hosts_do_not_wait_for_each_other() -> None:
    sleeper = RecordingSleeper()
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=4, min_interval_s=5.0), sleeper=sleeper
    )
    await limiter.acquire("a.test")
    await limiter.acquire("b.test")
    assert sleeper.sleeps == []  # independent hosts start immediately


@pytest.mark.asyncio
async def test_host_overrides_replace_defaults() -> None:
    sleeper = RecordingSleeper()
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=4, min_interval_s=5.0),
        overrides={"fast.test": HostLimit(max_concurrent=2, min_interval_s=0.0)},
        sleeper=sleeper,
    )
    # slow.test uses the default interval: its second start waits.
    await limiter.acquire("slow.test")
    await limiter.acquire("slow.test")
    # fast.test overrides the interval: consecutive starts never wait.
    await limiter.acquire("fast.test")
    await limiter.acquire("fast.test")
    # Exactly one wait was produced, by slow.test's second start.
    assert len(sleeper.sleeps) == 1


@pytest.mark.asyncio
async def test_host_concurrency_cap_serializes_requests() -> None:
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=1, min_interval_s=0.0),
        global_max_concurrent=10,
    )
    acquired = asyncio.Event()

    async def holder() -> None:
        lease = await limiter.acquire("site.test")
        acquired.set()
        await asyncio.sleep(3600)  # never releases inside this test
        lease.release()

    holder_task = asyncio.create_task(holder())
    await acquired.wait()
    second = asyncio.create_task(limiter.acquire("site.test"))
    await asyncio.sleep(0)
    # The second acquire is still waiting for the host slot.
    assert not second.done()
    second.cancel()
    holder_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder_task


@pytest.mark.asyncio
async def test_global_cap_limits_across_hosts() -> None:
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=10, min_interval_s=0.0),
        global_max_concurrent=1,
    )
    acquired = asyncio.Event()

    async def holder() -> None:
        lease = await limiter.acquire("a.test")
        acquired.set()
        await asyncio.sleep(3600)
        lease.release()

    holder_task = asyncio.create_task(holder())
    await acquired.wait()
    second = asyncio.create_task(limiter.acquire("b.test"))
    await asyncio.sleep(0)
    assert not second.done()
    second.cancel()
    holder_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder_task
