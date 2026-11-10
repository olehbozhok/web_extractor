"""Service-layer types: request context and pipeline outcome.

``FetchOutcome`` is what MCP tools and the web UI consume; it always
describes one pipeline run over one URL, success or failure.
"""

from dataclasses import dataclass, field

from web_extractor.domain.content_type import DocumentKind
from web_extractor.domain.extract import PageMetadata
from web_extractor.domain.listing_links import LinkItem
from web_extractor.domain.types import (
    AgentId,
    DetailLevel,
    ErrorKind,
    FetcherName,
    PageFormat,
    RenderMode,
    Status,
    TaskId,
    ToolName,
)


@dataclass(frozen=True, slots=True)
class ServiceRequest:
    """Everything the pipeline needs beyond the URL itself."""

    raw_url: str
    fresh: bool
    render: RenderMode
    page_format: PageFormat
    detail: DetailLevel
    agent: AgentId
    tool: ToolName
    request_id: str
    task: TaskId | None = None
    mcp_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """Result of one pipeline run for one URL.

    ``text`` is present only when the pipeline extracted content and the
    caller asked for text (``check_urls`` does not). ``from_cache`` is true
    when this request was answered without a new network fetch, including
    single-flight joiners that piggybacked on another request's fetch.
    """

    status: Status
    url: str
    final_url: str | None = None
    http_status: int | None = None
    fetched_at: str | None = None
    """UTC ISO 8601 timestamp of the (possibly cached) fetch."""
    from_cache: bool = False
    fetcher: FetcherName | None = None
    text: str | None = None
    text_format: PageFormat | None = None
    metadata: PageMetadata = field(default_factory=PageMetadata)
    content_sha256: str | None = None
    error_kind: ErrorKind | None = None
    error_message: str | None = None
    document: DocumentKind | None = None
    """What the body is (sniffed), None when there is no body (fetch errors)."""
    content_type: str | None = None
    """The media type the server declared; may disagree with ``document``."""
    listing_links: tuple[LinkItem, ...] | None = None
    """Same-site anchor links derived from the raw HTML; present (possibly
    empty) only for ``ok`` html pages, None otherwise. Computed on every
    outcome build, never persisted to the cache."""
