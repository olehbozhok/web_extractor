"""Statistics aggregation tests over seeded events."""

from typing import NamedTuple

import pytest

from web_extractor.domain.records import Event
from web_extractor.domain.types import FetcherName, Status
from web_extractor.storage import (
    Database,
    EventRepository,
    NamedCount,
    collect_period_stats,
)


class _Case(NamedTuple):
    """Seed event fields that differ from the defaults."""

    ts: str
    agent: str
    url: str
    status: Status = "ok"
    http_status: int | None = 200
    cache_hit: bool = False
    fetcher: FetcherName | None = "http"
    duration_ms: int = 100
    task: str | None = None


def _event(request_id: str, case: _Case) -> Event:
    return Event(
        ts=case.ts,
        agent=case.agent,
        task=case.task,
        mcp_session_id=None,
        request_id=request_id,
        tool="fetch_page",
        url=case.url,
        final_url=case.url,
        cache_hit=case.cache_hit,
        fresh=True,
        fetcher=case.fetcher,
        status=case.status,
        http_status=case.http_status,
        duration_ms=case.duration_ms,
        queue_ms=0,
        size_bytes=None,
        content_sha256=None,
        error_kind=None,
    )


_SEED = [
    _Case(
        ts="2026-11-09T10:00:00+00:00",
        agent="alpha",
        url="https://news.example.org/a",
        duration_ms=50,
        cache_hit=True,
        task="t1",
    ),
    _Case(
        ts="2026-11-09T11:00:00+00:00",
        agent="alpha",
        url="https://news.example.org/b",
        duration_ms=150,
    ),
    _Case(
        ts="2026-11-09T12:00:00+00:00",
        agent="unknown",
        url="https://docs.example.net/c",
        status="blocked",
        http_status=403,
        duration_ms=2000,
    ),
    _Case(
        ts="2026-11-10T12:00:00+00:00",
        agent="beta",
        url="https://docs.example.net/c",
        status="needs_browser",
        http_status=None,
        fetcher=None,
        duration_ms=300,
    ),
]


@pytest.mark.asyncio
async def test_period_stats_aggregates(database: Database) -> None:
    repo = EventRepository(database)
    for number, case in enumerate(_SEED):
        await repo.append(_event(f"r{number}", case))

    stats = await collect_period_stats(
        database,
        since_iso="2026-11-09T00:00:00+00:00",
        until_iso="2026-11-09T23:59:59+00:00",
    )
    assert stats.total_events == 3
    assert stats.cache_hits == 1
    assert stats.by_status == {"blocked": 1, "ok": 2}
    assert stats.by_http_status == {"200": 2, "403": 1}
    assert stats.by_fetcher == {"http": 3}
    assert stats.needs_browser_count == 0
    assert stats.unknown_agent_count == 1
    assert stats.top_agents == (NamedCount("alpha", 2), NamedCount("unknown", 1))
    assert stats.top_domains[0].name == "news.example.org"
    assert stats.top_domains[0].count == 2
    assert stats.duration_p50_ms == 150
    assert stats.duration_p95_ms == 2000


@pytest.mark.asyncio
async def test_period_filters_outside_events(database: Database) -> None:
    repo = EventRepository(database)
    for number, case in enumerate(_SEED):
        await repo.append(_event(f"r{number}", case))

    stats = await collect_period_stats(
        database,
        since_iso="2026-11-10T00:00:00+00:00",
        until_iso="2026-11-10T23:59:59+00:00",
    )
    assert stats.total_events == 1
    assert stats.needs_browser_count == 1
    assert stats.by_http_status == {"none": 1}


@pytest.mark.asyncio
async def test_empty_period_gives_zeroed_stats(database: Database) -> None:
    stats = await collect_period_stats(
        database,
        since_iso="2026-11-09T00:00:00+00:00",
        until_iso="2026-11-09T23:59:59+00:00",
    )
    assert stats.total_events == 0
    assert stats.top_domains == ()
    assert stats.duration_p50_ms is None
    assert stats.duration_p95_ms is None
