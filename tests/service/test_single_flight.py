"""Single-flight rules: waiter cancellation and the fresh/no-join rule."""

import asyncio

import pytest

from tests.service.conftest import ServiceHarness, service_request
from tests.service.service_helpers import extracted, fetched_ok, normalized
from web_extractor.service.single_flight import SingleFlight, flight_key
from web_extractor.service.types import FetchOutcome


def _outcome() -> FetchOutcome:
    return FetchOutcome(status="ok", url="https://site.test/page")


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_shared_task() -> None:
    """docs/02 section 4: a cancelled waiter leaves the shared fetch alone."""
    flights: SingleFlight[FetchOutcome] = SingleFlight()
    started = asyncio.Event()
    may_finish = asyncio.Event()
    calls = 0

    async def factory() -> FetchOutcome:
        nonlocal calls
        calls += 1
        started.set()
        await may_finish.wait()
        return _outcome()

    runner = asyncio.create_task(flights.run("https://site.test/page", factory))
    await started.wait()
    waiter_a = asyncio.create_task(flights.run("https://site.test/page", factory))
    waiter_b = asyncio.create_task(flights.run("https://site.test/page", factory))
    waiter_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_a
    may_finish.set()
    runner_result, runner_self = await runner
    waiter_b_result, waiter_b_self = await waiter_b
    assert calls == 1
    assert runner_self is True
    assert waiter_b_self is False
    assert runner_result == waiter_b_result


@pytest.mark.asyncio
async def test_fresh_request_does_not_join_older_non_fresh_task() -> None:
    """A fresh=true request runs its own fetch even while a non-fresh one
    for the same URL is in flight (docs/02 section 4)."""
    flights: SingleFlight[FetchOutcome] = SingleFlight()
    entered = asyncio.Event()
    may_finish = asyncio.Event()
    calls = 0

    async def factory() -> FetchOutcome:
        nonlocal calls
        calls += 1
        entered.set()
        await may_finish.wait()
        return _outcome()

    shared = asyncio.create_task(flights.run("https://site.test/page", factory))
    await entered.wait()
    fresh = asyncio.create_task(
        flights.run(flight_key("https://site.test/page", fresh=True), factory)
    )
    # Yield the event loop (no wall-clock sleeping) until the fresh fetch has
    # actually started its own run.
    for _ in range(100):
        if calls == 2:
            break
        await asyncio.sleep(0)
    assert calls == 2  # no join: the fresh fetch started independently
    may_finish.set()
    await asyncio.gather(shared, fresh)


@pytest.mark.asyncio
async def test_fresh_requests_join_each_other() -> None:
    flights: SingleFlight[FetchOutcome] = SingleFlight()
    entered = asyncio.Event()
    may_finish = asyncio.Event()
    calls = 0

    async def factory() -> FetchOutcome:
        nonlocal calls
        calls += 1
        entered.set()
        await may_finish.wait()
        return _outcome()

    key = flight_key("https://site.test/page", fresh=True)
    first = asyncio.create_task(flights.run(key, factory))
    await entered.wait()
    second = asyncio.create_task(flights.run(key, factory))
    may_finish.set()
    await asyncio.gather(first, second)
    assert calls == 1


@pytest.mark.asyncio
async def test_pipeline_fresh_rule_via_service(harness: ServiceHarness) -> None:
    """Integration: a fresh service request triggers a second fetch while
    the non-fresh one is gated in flight."""
    url = "https://site.test/page"
    harness.fetcher.gated = True
    harness.fetcher.script(fetched_ok(normalized(url)))
    harness.extractor.script(extracted())

    shared = asyncio.create_task(harness.service.fetch(service_request(url)))
    await harness.fetcher.entered.wait()
    harness.fetcher.script(fetched_ok(normalized(url)))
    harness.extractor.script(extracted())
    fresh = asyncio.create_task(harness.service.fetch(service_request(url, fresh=True)))
    # Yield the event loop (no wall-clock sleeping) until the fresh fetch
    # has actually issued its own network call.
    for _ in range(100):
        if len(harness.fetcher.calls) == 2:
            break
        await asyncio.sleep(0)
    assert len(harness.fetcher.calls) == 2
    harness.fetcher.release.set()
    await asyncio.gather(shared, fresh)
