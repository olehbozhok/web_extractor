"""Cursor issuing and resolution for MCP pagination (docs/03, section 3.3).

A cursor points inside one specific version of one page's text. Resolution
re-reads the cached text (re-extracting from stored HTML when the cached
format differs), verifies the text version by sha256, and either returns
the next slice or explains what to do: unknown (never existed / expired)
or stale (the page changed since the cursor was issued).
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from web_extractor.domain.content_quality import ContentQuality, assess_content
from web_extractor.domain.content_type import DocumentKind, sniff_document
from web_extractor.domain.extract import Extracted, PageMetadata, parse_metadata_json
from web_extractor.domain.records import PageRecord
from web_extractor.domain.ttl import to_iso_utc
from web_extractor.domain.types import Clock, PageFormat
from web_extractor.extractors.base import Extractor
from web_extractor.storage.cache_repository import CacheRepository
from web_extractor.storage.cursor_repository import (
    CursorDraft,
    CursorRepository,
    CursorState,
)


@dataclass(frozen=True, slots=True)
class CursorText:
    """A successful cursor resolution: one slice plus continuation info."""

    state: CursorState
    slice_text: str
    total_chars: int
    has_more: bool
    next_cursor_id: str | None
    """Issued only when ``has_more``; points at the end of this slice."""
    metadata: PageMetadata
    """The page's metadata, so a continuation does not lose title and author."""
    content_quality: ContentQuality
    """The verdict on the whole text, not on the slice."""
    document: DocumentKind | None
    """What the cached body is (sniffed)."""


@dataclass(frozen=True, slots=True)
class UnknownCursor:
    """The cursor never existed or its TTL passed."""


@dataclass(frozen=True, slots=True)
class StaleCursor:
    """The page text changed after the cursor was issued."""

    url_key: str


type CursorRead = CursorText | UnknownCursor | StaleCursor


class CursorService:
    """Issues deterministic cursors and resolves them against the cache."""

    def __init__(
        self,
        cache: CacheRepository,
        cursors: CursorRepository,
        extractor: Extractor,
        clock: Clock,
        cursor_ttl: timedelta,
    ) -> None:
        self._cache = cache
        self._cursors = cursors
        self._extractor = extractor
        self._clock = clock
        self._cursor_ttl = cursor_ttl

    async def issue(
        self,
        url_key: str,
        full_text: str,
        page_format: PageFormat,
        *,
        offset: int,
        max_chars: int,
    ) -> str:
        """Create (or reuse) the cursor for this reading position."""
        now = self._clock.now()
        state = await self._cursors.create(
            CursorDraft(
                url_key=url_key,
                text_sha256=text_version_sha(full_text),
                offset=offset,
                max_chars=max_chars,
                page_format=page_format,
                created_at=to_iso_utc(now),
                expires_at=to_iso_utc(now + self._cursor_ttl),
            )
        )
        return state.id

    async def read(self, cursor_id: str) -> CursorRead:
        """Resolve a cursor into the next text slice, or explain the failure."""
        state = await self._cursors.get(cursor_id)
        if state is None:
            return UnknownCursor()
        now = self._clock.now()
        if to_iso_utc(now) >= state.expires_at:
            return UnknownCursor()

        record = await self._cache.get(state.url_key)
        if record is None:
            return StaleCursor(url_key=state.url_key)
        full_text = await self._text_in_format(record, state.format)
        if full_text is None:
            return StaleCursor(url_key=state.url_key)
        if text_version_sha(full_text) != state.text_sha256:
            return StaleCursor(url_key=state.url_key)

        await self._cursors.touch(cursor_id, to_iso_utc(now + self._cursor_ttl))
        end = state.offset + state.max_chars
        has_more = end < len(full_text)
        next_cursor = (
            await self.issue(
                state.url_key,
                full_text,
                state.format,
                offset=end,
                max_chars=state.max_chars,
            )
            if has_more
            else None
        )
        return CursorText(
            state=state,
            slice_text=full_text[state.offset : end],
            total_chars=len(full_text),
            has_more=has_more,
            next_cursor_id=next_cursor,
            metadata=parse_metadata_json(record.metadata_json),
            content_quality=assess_content(full_text),
            document=sniff_document(record.html) if record.html is not None else None,
        )

    async def _text_in_format(self, record: PageRecord, page_format: PageFormat) -> str | None:
        """The page text in ``page_format``, re-extracting from HTML if needed."""
        if record.text is None:
            return None
        if record.text_format == page_format:
            return record.text
        if record.html is None:
            return None
        extract_result = await self._extractor.extract(record.html, record.final_url, page_format)
        if isinstance(extract_result, Extracted):
            return extract_result.text
        return None


def text_version_sha(text: str) -> str:
    """The sha256 hex digest identifying one version of a page's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_version_short(text: str) -> str:
    """The 8-hex prefix reported as ``text_version`` in tool responses."""
    return text_version_sha(text)[:8]
