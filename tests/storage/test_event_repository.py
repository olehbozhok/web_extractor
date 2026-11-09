"""Event repository tests: append, persistence, concurrent writes."""

import asyncio

import pytest

from web_extractor.domain.records import Event
from web_extractor.storage import Database, EventRepository


def _event(request_id: str) -> Event:
    return Event(
        ts="2026-11-09T12:00:00+00:00",
        agent="tester",
        task="task-1",
        mcp_session_id=None,
        request_id=request_id,
        tool="fetch_page",
        url="https://example.com/",
        final_url="https://example.com/",
        cache_hit=False,
        fresh=True,
        fetcher="http",
        status="ok",
        http_status=200,
        duration_ms=120,
        queue_ms=5,
        size_bytes=1024,
        content_sha256=None,
        error_kind=None,
    )


@pytest.mark.asyncio
async def test_append_and_count(database: Database) -> None:
    repo = EventRepository(database)
    assert await repo.count() == 0
    await repo.append(_event("r1"))
    await repo.append(_event("r2"))
    assert await repo.count() == 2


@pytest.mark.asyncio
async def test_concurrent_appends_all_persisted(database: Database) -> None:
    """WAL + busy_timeout must serialize concurrent writers without loss."""
    repo = EventRepository(database)
    total = 20
    await asyncio.gather(*(repo.append(_event(f"r{number}")) for number in range(total)))
    assert await repo.count() == total
