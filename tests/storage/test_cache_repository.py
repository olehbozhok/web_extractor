"""Cache repository tests: roundtrip, overwrite, delete."""

import pytest

from web_extractor.domain.records import PageRecord
from web_extractor.storage import CacheRepository, Database

_METADATA_JSON = '{"title": "Example"}'


def _record(url_key: str, text: str | None) -> PageRecord:
    return PageRecord(
        url_key=url_key,
        final_url=url_key,
        status="ok",
        http_status=200,
        fetcher="http",
        fetched_at="2026-11-09T12:00:00+00:00",
        expires_at="2026-11-10T12:00:00+00:00",
        content_sha256=None,
        html=b"<html><body>hi</body></html>",
        text=text,
        text_format="text" if text is not None else None,
        metadata_json=_METADATA_JSON,
        error_kind=None,
        error_message=None,
    )


@pytest.mark.asyncio
async def test_roundtrip_and_missing(database: Database) -> None:
    repo = CacheRepository(database)
    record = _record("https://example.com/", "hello")
    await repo.put(record)
    assert await repo.get("https://example.com/") == record
    assert await repo.get("https://other.example.com/") is None


@pytest.mark.asyncio
async def test_put_overwrites_previous_entry(database: Database) -> None:
    repo = CacheRepository(database)
    await repo.put(_record("https://example.com/", "old"))
    updated = _record("https://example.com/", "new")
    await repo.put(updated)
    assert await repo.get("https://example.com/") == updated


@pytest.mark.asyncio
async def test_delete_reports_presence(database: Database) -> None:
    repo = CacheRepository(database)
    await repo.put(_record("https://example.com/", "hello"))
    assert await repo.delete("https://example.com/") is True
    assert await repo.delete("https://example.com/") is False
    assert await repo.get("https://example.com/") is None


@pytest.mark.asyncio
async def test_error_entry_without_html_roundtrips(database: Database) -> None:
    repo = CacheRepository(database)
    record = PageRecord(
        url_key="https://blocked.example.com/",
        final_url="https://blocked.example.com/",
        status="blocked",
        http_status=403,
        fetcher="http",
        fetched_at="2026-11-09T12:00:00+00:00",
        expires_at="2026-11-09T13:00:00+00:00",
        content_sha256=None,
        html=None,
        text=None,
        text_format=None,
        metadata_json="{}",
        error_kind="blocked",
        error_message="403 forbidden",
    )
    await repo.put(record)
    assert await repo.get(record.url_key) == record
