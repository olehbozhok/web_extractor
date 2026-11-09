"""Fixtures for service-layer tests: a wired FetchService over a temp DB."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio

from tests.service.service_helpers import FakeClock, ScriptedExtractor, ScriptedFetcher
from web_extractor.domain.types import AgentId, PageFormat, TaskId
from web_extractor.service.config import ServiceConfig
from web_extractor.service.domain_limits import DomainLimiter, HostLimit
from web_extractor.service.pipeline import FetchService, FetchServiceDeps
from web_extractor.service.single_flight import SingleFlight
from web_extractor.service.types import ServiceRequest
from web_extractor.storage.cache_repository import CacheRepository
from web_extractor.storage.database import Database
from web_extractor.storage.event_repository import EventRepository


@dataclass
class ServiceHarness:
    """Everything a pipeline test needs in one place."""

    service: FetchService
    fetcher: ScriptedFetcher
    extractor: ScriptedExtractor
    clock: FakeClock
    database: Database

    async def event_rows(self) -> list[dict[str, object]]:
        """All journal events as plain dicts, in insertion order."""
        async with self.database.connection.execute("SELECT * FROM events ORDER BY id") as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[ServiceHarness]:
    database = Database(str(tmp_path / "service.db"))
    await database.connect()
    fetcher = ScriptedFetcher()
    extractor = ScriptedExtractor()
    clock = FakeClock()
    limiter = DomainLimiter(
        default_limit=HostLimit(max_concurrent=2, min_interval_s=0.0),
        global_max_concurrent=5,
    )
    service = FetchService(
        FetchServiceDeps(
            fetcher=fetcher,
            extractor=extractor,
            cache=CacheRepository(database),
            events=EventRepository(database),
            limiter=limiter,
            flights=SingleFlight(),
            clock=clock,
            config=ServiceConfig(),
        )
    )
    yield ServiceHarness(
        service=service, fetcher=fetcher, extractor=extractor, clock=clock, database=database
    )
    await database.close()


def service_request(
    raw_url: str,
    *,
    fresh: bool = False,
    page_format: PageFormat = "text",
    task: str | None = None,
    request_id: str = "req-1",
) -> ServiceRequest:
    """A default ServiceRequest with common overrides."""
    return ServiceRequest(
        raw_url=raw_url,
        fresh=fresh,
        render="static",
        page_format=page_format,
        detail="concise",
        agent=AgentId("tester"),
        tool="fetch_page",
        request_id=request_id,
        task=TaskId(task) if task is not None else None,
    )
