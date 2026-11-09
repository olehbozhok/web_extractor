"""Events and Agents page tests (S7) plus the underlying query layer."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.service.service_helpers import FakeClock
from web_extractor.config import Settings
from web_extractor.domain.records import Event
from web_extractor.domain.types import Status
from web_extractor.mcp_api.app import create_app
from web_extractor.storage.database import Database
from web_extractor.storage.event_repository import EventRepository

_NOW_ISO = "2026-11-09T12:00:00+00:00"


def _event(
    request_id: str,
    agent: str,
    url: str,
    *,
    status: Status = "ok",
    duration_ms: int = 100,
    task: str | None = None,
    cache_hit: bool = False,
) -> Event:
    return Event(
        ts=_NOW_ISO,
        agent=agent,
        task=task,
        mcp_session_id=None,
        request_id=request_id,
        tool="fetch_page",
        url=url,
        final_url=url,
        cache_hit=cache_hit,
        fresh=False,
        fetcher="http",
        status=status,
        http_status=200,
        duration_ms=duration_ms,
        queue_ms=0,
        size_bytes=500,
        content_sha256=None,
        error_kind=None,
    )


_SEED: tuple[Event, ...] = (
    _event("r1", "alpha", "https://news.example.org/a", duration_ms=300, task="t1"),
    _event("r2", "alpha", "https://news.example.org/a", cache_hit=True, duration_ms=50),
    _event("r3", "beta", "https://docs.example.net/b", status="blocked", duration_ms=900),
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over an app with three seeded events from two agents."""
    settings = Settings(db_path=str(tmp_path / "web.db"))

    async def _seed() -> None:
        db = Database(settings.db_path)
        await db.connect()
        repo = EventRepository(db)
        for event in _SEED:
            await repo.append(event)
        await db.close()

    asyncio.run(_seed())
    app = create_app(settings, clock=FakeClock())
    with TestClient(app) as test_client:
        yield test_client


def test_events_page_lists_rows(client: TestClient) -> None:
    html = client.get("/events").text
    assert "alpha" in html and "beta" in html
    assert "news.example.org" in html
    assert "3 events" in html


def test_events_filter_by_agent(client: TestClient) -> None:
    html = client.get("/events", params={"agent": "beta"}).text
    assert "beta" in html
    assert "alpha" not in html.split('<div id="events-table">')[1]


def test_events_filter_by_status(client: TestClient) -> None:
    html = client.get("/events", params={"status": "blocked"}).text
    body = html.split('<div id="events-table">')[1]
    assert "docs.example.net" in body
    assert "news.example.org" not in body


def test_events_sort_by_duration_desc(client: TestClient) -> None:
    html = client.get("/events", params={"sort": "duration_ms", "descending": "true"}).text
    positions = [html.find(f'<td class="num">{duration}</td>') for duration in (900, 300, 50)]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)


def test_events_pagination(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "pages.db"))

    async def _seed() -> None:
        db = Database(settings.db_path)
        await db.connect()
        repo = EventRepository(db)
        for number in range(30):
            await repo.append(_event(f"r{number}", "alpha", f"https://x.test/{number}"))
        await db.close()

    asyncio.run(_seed())
    app = create_app(settings, clock=FakeClock())
    with TestClient(app) as test_client:
        page_one = test_client.get("/events", params={"page": 1}).text
        page_two = test_client.get("/events", params={"page": 2}).text
    assert "30 events; page 1 of 2" in page_one
    assert "next" in page_one.lower()
    assert "prev" in page_two.lower()


def test_events_htmx_partial(client: TestClient) -> None:
    response = client.get("/events", headers={"HX-Request": "true"}, params={"agent": "beta"})
    assert response.status_code == 200
    assert "<html" not in response.text  # partial, not a full page
    assert "beta" in response.text


def test_events_bad_sort_is_400(client: TestClient) -> None:
    assert client.get("/events", params={"sort": "DROP TABLE"}).status_code == 400


def test_events_page_escapes_seeded_html(tmp_path: Path) -> None:
    """Untrusted values from the journal must render escaped (S: XSS)."""
    settings = Settings(db_path=str(tmp_path / "xss.db"))

    async def _seed() -> None:
        db = Database(settings.db_path)
        await db.connect()
        repo = EventRepository(db)
        await repo.append(_event("rx", "evil", "https://x.test/<script>alert(1)</script>"))
        await db.close()

    asyncio.run(_seed())
    app = create_app(settings, clock=FakeClock())
    with TestClient(app) as test_client:
        html = test_client.get("/events").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_agents_page_breakdown(client: TestClient) -> None:
    html = client.get("/agents").text
    assert "alpha" in html and "beta" in html
    # alpha: 2 requests (one cache hit, one fresh), 2 distinct urls? one url twice.
    assert "t1" in html  # top tasks table


def test_agents_period_filter(client: TestClient) -> None:
    html = client.get("/agents", params={"period": "30d"}).text
    assert "alpha" in html
