"""Persistent records: a cache entry and a journal event.

Both mirror their SQLite tables (docs/02_architecture.md, section 6).
Timestamps are UTC ISO 8601 strings with timezone, exactly as stored.
"""

from dataclasses import dataclass

from web_extractor.domain.types import (
    ErrorKind,
    FetcherName,
    PageFormat,
    Status,
    ToolName,
)


@dataclass(frozen=True, slots=True)
class PageRecord:
    """A stored cache entry: everything the pipeline knows about one URL key.

    ``url_key`` is the normalized URL the entry is cached under,
    ``final_url`` the post-redirect URL. ``html`` is None for error entries;
    ``text`` is None when extraction failed.
    """

    url_key: str
    final_url: str
    status: Status
    http_status: int | None
    fetcher: FetcherName
    fetched_at: str
    expires_at: str
    content_sha256: str | None
    html: bytes | None
    text: str | None
    text_format: PageFormat | None
    """Which PageFormat ``text`` was extracted in; None for error entries."""
    metadata_json: str
    error_kind: ErrorKind | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class Event:
    """One append-only journal entry; matches the ``events`` table."""

    ts: str
    agent: str
    """Agent identifier, ``unknown`` when the client did not provide one."""
    task: str | None
    mcp_session_id: str | None
    request_id: str
    tool: ToolName
    url: str
    final_url: str | None
    cache_hit: bool
    fresh: bool
    fetcher: FetcherName | None
    status: Status
    http_status: int | None
    duration_ms: int
    queue_ms: int
    size_bytes: int | None
    content_sha256: str | None
    error_kind: ErrorKind | None
