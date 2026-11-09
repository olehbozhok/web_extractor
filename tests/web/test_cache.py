"""Cache page tests: list, escaped detail, reload and delete (S7)."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.service.service_helpers import (
    FakeClock,
    ScriptedExtractor,
    ScriptedFetcher,
    extracted,
    fetched_ok,
    normalized,
)
from web_extractor.config import Settings
from web_extractor.domain.records import PageRecord
from web_extractor.mcp_api.app import create_app
from web_extractor.storage.cache_repository import CacheRepository
from web_extractor.storage.database import Database

_URL = "https://site.test/article"


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[tuple[TestClient, ScriptedFetcher, ScriptedFetcher]]:
    """A TestClient plus the scripted fetcher used for reload calls."""
    settings = Settings(db_path=str(tmp_path / "web.db"))
    fetcher = ScriptedFetcher()
    extractor = ScriptedExtractor()
    app = create_app(settings, fetcher=fetcher, extractor=extractor, clock=FakeClock())

    async def _seed() -> None:
        db = Database(settings.db_path)
        await db.connect()
        cache = CacheRepository(db)
        await cache.put(
            PageRecord(
                url_key=_URL,
                final_url=_URL,
                status="ok",
                http_status=200,
                fetcher="http",
                fetched_at="2026-11-09T12:00:00+00:00",
                expires_at="2026-11-10T12:00:00+00:00",
                content_sha256=None,
                html=b"<html><body><script>alert('owned')</script></body></html>",
                text="page text",
                text_format="text",
                metadata_json='{"title": "T"}',
                error_kind=None,
                error_message=None,
            )
        )
        await db.close()

    asyncio.run(_seed())
    with TestClient(app) as test_client:
        yield test_client, fetcher, extractor


def test_cache_list_shows_entry(
    harness: tuple[TestClient, ScriptedFetcher, ScriptedFetcher],
) -> None:
    client, _fetcher, _extractor = harness
    html = client.get("/cache").text
    assert "1 entries" in html
    assert _URL in html
    assert "reload" in html and "delete" in html


def test_cache_detail_escapes_saved_html(
    harness: tuple[TestClient, ScriptedFetcher, ScriptedFetcher],
) -> None:
    """The stored page HTML must be displayed escaped, never executed (S: XSS)."""
    client, _fetcher, _extractor = harness
    response = client.get("/cache/detail", params={"url_key": _URL})
    assert response.status_code == 200
    html = response.text
    assert "<script>alert('owned')</script>" not in html
    assert "&lt;script&gt;" in html
    assert "page text" in html  # extracted text shown too


def test_cache_detail_missing_is_404(
    harness: tuple[TestClient, ScriptedFetcher, ScriptedFetcher],
) -> None:
    client, _fetcher, _extractor = harness
    response = client.get("/cache/detail", params={"url_key": "https://none.test/"})
    assert response.status_code == 404


def test_cache_delete_removes_entry(
    harness: tuple[TestClient, ScriptedFetcher, ScriptedFetcher],
) -> None:
    client, _fetcher, _extractor = harness
    response = client.post("/cache/delete", data={"url_key": _URL}, follow_redirects=False)
    assert response.status_code == 303
    html = client.get("/cache").text
    assert "0 entries" in html


def test_cache_reload_refetches_and_journals(
    harness: tuple[TestClient, ScriptedFetcher, ScriptedFetcher],
) -> None:
    client, fetcher, extractor = harness
    fetcher.script(fetched_ok(normalized(_URL)))
    extractor.script(extracted("fresh page text", page_format="text"))

    response = client.post("/cache/reload", data={"url_key": _URL}, follow_redirects=False)
    assert response.status_code == 303
    assert len(fetcher.calls) == 1  # reload hit the network with fresh=true

    detail = client.get("/cache/detail", params={"url_key": _URL}).text
    assert "fresh page text" in detail
