"""Cursor repository tests: determinism, lookup, TTL, purge."""

import pytest

from web_extractor.domain.types import PageFormat
from web_extractor.storage import (
    CURSOR_ALPHABET,
    CursorDraft,
    CursorRepository,
    CursorState,
    Database,
)

_URL_KEY = "https://example.com/"
_TEXT_SHA256 = "a" * 64
_MAX_CHARS = 12000
_PAGE_FORMAT: PageFormat = "markdown"
_CREATED_AT = "2026-11-09T12:00:00+00:00"


async def _create(
    repo: CursorRepository,
    *,
    offset: int,
    expires_at: str = "2026-11-10T12:00:00+00:00",
) -> CursorState:
    draft = CursorDraft(
        url_key=_URL_KEY,
        text_sha256=_TEXT_SHA256,
        offset=offset,
        max_chars=_MAX_CHARS,
        page_format=_PAGE_FORMAT,
        created_at=_CREATED_AT,
        expires_at=expires_at,
    )
    return await repo.create(draft)


@pytest.mark.asyncio
async def test_create_is_deterministic(database: Database) -> None:
    repo = CursorRepository(database)
    first = await _create(repo, offset=12000)
    second = await _create(repo, offset=12000)
    assert first == second
    assert len(first.id) == 6
    assert set(first.id) <= set(CURSOR_ALPHABET)


@pytest.mark.asyncio
async def test_different_state_gives_different_cursor(database: Database) -> None:
    repo = CursorRepository(database)
    first = await _create(repo, offset=12000)
    second = await _create(repo, offset=24000)
    assert first.id != second.id


@pytest.mark.asyncio
async def test_get_unknown_cursor(database: Database) -> None:
    repo = CursorRepository(database)
    assert await repo.get("zzzzzz") is None


@pytest.mark.asyncio
async def test_touch_extends_ttl(database: Database) -> None:
    repo = CursorRepository(database)
    created = await _create(repo, offset=12000)
    assert await repo.touch(created.id, "2026-11-11T12:00:00+00:00") is True
    loaded = await repo.get(created.id)
    assert loaded is not None
    assert loaded.expires_at == "2026-11-11T12:00:00+00:00"
    assert await repo.touch("missing", "2026-11-11T12:00:00+00:00") is False


@pytest.mark.asyncio
async def test_purge_expired_keeps_live_ones(database: Database) -> None:
    repo = CursorRepository(database)
    live = await _create(repo, offset=12000)
    expired = await _create(repo, offset=24000, expires_at="2026-11-09T13:00:00+00:00")
    # Purge moment is after the expired cursor's TTL but before the live one's.
    assert await repo.purge_expired("2026-11-10T11:00:00+00:00") == 1
    assert await repo.get(expired.id) is None
    assert await repo.get(live.id) is not None
