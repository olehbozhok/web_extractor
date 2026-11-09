"""Dashboard page tests: response-level checks (S7, docs/01 section 4.7)."""

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
    cache_hit: bool = False,
) -> Event:
    return Event(
        ts=_NOW_ISO,
        agent=agent,
        task=None,
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
        duration_ms=100,
        queue_ms=0,
        size_bytes=500,
        content_sha256=None,
        error_kind=None,
    )


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over an app whose journal holds two seeded events."""
    settings = Settings(db_path=str(tmp_path / "web.db"))

    async def _seed() -> None:
        db = Database(settings.db_path)
        await db.connect()
        repo = EventRepository(db)
        await repo.append(_event("r1", "alpha", "https://news.example.org/a"))
        await repo.append(_event("r2", "alpha", "https://news.example.org/a", cache_hit=True))
        await db.close()

    asyncio.run(_seed())
    app = create_app(settings, clock=FakeClock())
    with TestClient(app) as client:
        yield client


def test_dashboard_renders_stats(seeded_client: TestClient) -> None:
    response = seeded_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Dashboard" in html
    assert "50.0%" in html  # hit rate: 1 cache hit of 2 requests
    assert "alpha" in html
    assert "news.example.org" in html


def test_dashboard_empty_period(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "empty.db"))
    app = create_app(settings, clock=FakeClock())
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "No requests recorded" in response.text


def test_dashboard_unknown_period_is_400(seeded_client: TestClient) -> None:
    assert seeded_client.get("/?period=bogus").status_code == 400


def test_htmx_is_self_hosted(seeded_client: TestClient) -> None:
    response = seeded_client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text
    assert "cdn.jsdelivr" not in response.text and "unpkg.com" not in response.text
