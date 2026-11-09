"""The fetch pipeline: normalize -> cache -> single-flight -> limits ->
fetch -> extract -> store -> journal.

Every request, including ones rejected by validation or SSRF, ends with an
event in the journal (01_requirements.md, 4.6). Events are appended inline:
one short awaited INSERT before the response is returned, so nothing is
lost on shutdown and no background queue is needed.

Cache hits answer without touching the fetcher. Fresh requests bypass the
cache and run in the fresh key space of single-flight, so they never join
an older in-flight non-fresh task. Error entries are cached with a shorter
TTL; a 429 with Retry-After keeps its entry until that moment.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from web_extractor.domain.extract import (
    Extracted,
    ExtractionFailureKind,
    ExtractResult,
    PageMetadata,
)
from web_extractor.domain.fetch import Fetched, FetchFailure, FetchFailureKind, FetchRequest
from web_extractor.domain.records import Event, PageRecord
from web_extractor.domain.ttl import compute_expires_at, is_expired, parse_iso_utc, to_iso_utc
from web_extractor.domain.types import Clock, ErrorKind, PageFormat, Status
from web_extractor.domain.urls import NormalizedUrl, UrlParseError, parse_normalized_url
from web_extractor.extractors.base import Extractor
from web_extractor.fetchers.base import Fetcher
from web_extractor.service.config import ServiceConfig
from web_extractor.service.domain_limits import DomainLimiter
from web_extractor.service.single_flight import SingleFlight, flight_key
from web_extractor.service.types import FetchOutcome, ServiceRequest
from web_extractor.storage.cache_repository import CacheRepository
from web_extractor.storage.event_repository import EventRepository


@dataclass(frozen=True, slots=True)
class _FlightResult:
    """What the shared single-flight task carries back to all callers."""

    outcome: FetchOutcome
    queue_ms: int
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class _RequestMetrics:
    """Per-request journal metrics the callers know but the outcome does not."""

    queue_ms: int
    cache_hit: bool
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class FetchServiceDeps:
    """Constructor bundle for FetchService (composition root wires it)."""

    fetcher: Fetcher
    extractor: Extractor
    cache: CacheRepository
    events: EventRepository
    limiter: DomainLimiter
    flights: SingleFlight[_FlightResult]
    clock: Clock
    config: ServiceConfig


class FetchService:
    """Runs the full pipeline for one service request."""

    def __init__(self, deps: FetchServiceDeps) -> None:
        self._fetcher = deps.fetcher
        self._extractor = deps.extractor
        self._cache = deps.cache
        self._events = deps.events
        self._limiter = deps.limiter
        self._flights = deps.flights
        self._clock = deps.clock
        self._config = deps.config

    async def record_rejection(
        self, request: ServiceRequest, error_kind: ErrorKind, message: str
    ) -> FetchOutcome:
        """Journal a request rejected before the pipeline ran (bad arguments).

        The rejection never reaches the fetcher, so the MCP layer records
        the event through this method instead.
        """
        started = self._clock.now()
        outcome = FetchOutcome(
            status="error",
            url=request.raw_url.strip(),
            error_kind=error_kind,
            error_message=message,
        )
        await self._append_event(
            request, outcome, started, _RequestMetrics(queue_ms=0, cache_hit=False, size_bytes=None)
        )
        return outcome

    async def journal_read(
        self, request: ServiceRequest, outcome: FetchOutcome, started: datetime
    ) -> None:
        """Journal a request served entirely from the cache (read_more).

        Cursor reads never reach the fetcher, so the MCP layer journals
        them through this method with the outcome it built.
        """
        await self._append_event(
            request, outcome, started, _RequestMetrics(queue_ms=0, cache_hit=True, size_bytes=None)
        )

    async def fetch(self, request: ServiceRequest) -> FetchOutcome:
        """Run the pipeline; every request ends with exactly one event."""
        started = self._clock.now()
        url = parse_normalized_url(request.raw_url, self._config.tracking_params)
        if isinstance(url, UrlParseError):
            outcome = FetchOutcome(
                status="error",
                url=request.raw_url.strip(),
                error_kind="invalid_argument",
                error_message=url.message,
            )
            await self._append_event(
                request,
                outcome,
                started,
                _RequestMetrics(queue_ms=0, cache_hit=False, size_bytes=None),
            )
            return outcome

        if not request.fresh:
            cached = await self._cache.get(url.value)
            if cached is not None and not is_expired(
                parse_iso_utc(cached.expires_at), self._clock.now()
            ):
                outcome = await self._outcome_from_record(cached, request.page_format)
                size = len(cached.html) if cached.html is not None else None
                await self._append_event(
                    request,
                    outcome,
                    started,
                    _RequestMetrics(queue_ms=0, cache_hit=True, size_bytes=size),
                )
                return outcome

        flight_result, started_by_self = await self._flights.run(
            flight_key(url.value, fresh=request.fresh),
            lambda: self._fetch_extract_store(url, request),
        )
        metrics = (
            _RequestMetrics(
                queue_ms=flight_result.queue_ms,
                cache_hit=False,
                size_bytes=flight_result.size_bytes,
            )
            if started_by_self
            else _RequestMetrics(queue_ms=0, cache_hit=True, size_bytes=None)
        )
        await self._append_event(request, flight_result.outcome, started, metrics)
        return flight_result.outcome

    async def _fetch_extract_store(
        self, url: NormalizedUrl, request: ServiceRequest
    ) -> _FlightResult:
        """The shared work: one network fetch, extraction, cache write."""
        lease = await self._limiter.acquire(url.host)
        try:
            fetch_result = await self._fetcher.fetch(
                FetchRequest(
                    url=url,
                    fresh=request.fresh,
                    render=request.render,
                    agent=request.agent,
                    task=request.task,
                )
            )
        finally:
            lease.release()

        now = self._clock.now()
        previous = await self._cache.get(url.value)
        if isinstance(fetch_result, FetchFailure):
            record = self._failure_record(url, fetch_result, now)
        else:
            extract_result = await self._extractor.extract(
                fetch_result.body, fetch_result.final_url.value, request.page_format
            )
            record = self._record_for_fetched(
                url, fetch_result, extract_result, now, request.page_format
            )
        await self._cache.put(record)

        if (
            previous is not None
            and previous.content_sha256 is not None
            and record.content_sha256 is not None
            and previous.content_sha256 != record.content_sha256
        ):
            await self._events.append(self._content_changed_event(request, url, now))

        outcome = await self._outcome_from_record(record, request.page_format, from_cache=False)
        return _FlightResult(
            outcome=outcome,
            queue_ms=lease.queue_ms,
            size_bytes=len(record.html) if record.html is not None else None,
        )

    async def _outcome_from_record(
        self,
        record: PageRecord,
        page_format: PageFormat,
        *,
        from_cache: bool = True,
    ) -> FetchOutcome:
        """Build the outcome; re-extract from stored HTML when the cached
        text is in another format (01_requirements.md, 4.4)."""
        text = record.text
        text_format = record.text_format
        if record.html is not None and text is not None and text_format != page_format:
            extract_result = await self._extractor.extract(
                record.html, record.final_url, page_format
            )
            if isinstance(extract_result, Extracted):
                text = extract_result.text
                text_format = page_format
                await self._cache.put(replace(record, text=text, text_format=page_format))
        return FetchOutcome(
            status=record.status,
            url=record.url_key,
            final_url=record.final_url,
            http_status=record.http_status,
            fetched_at=record.fetched_at,
            from_cache=from_cache,
            fetcher=record.fetcher,
            text=text if record.status == "ok" else None,
            text_format=text_format if record.status == "ok" else None,
            metadata=_parse_metadata(record.metadata_json),
            content_sha256=record.content_sha256,
            error_kind=record.error_kind,
            error_message=record.error_message,
        )

    def _record_for_fetched(
        self,
        url: NormalizedUrl,
        fetched: Fetched,
        extract_result: ExtractResult,
        now: datetime,
        page_format: PageFormat,
    ) -> PageRecord:
        """Build the cache entry for a successful network fetch.

        Extraction failures still store the raw HTML (it may be worth
        re-extracting later) with the error-entry TTL.
        """
        sha = hashlib.sha256(fetched.body).hexdigest()
        expires_delta = self._config.cache_ttl
        if isinstance(extract_result, Extracted):
            status: Status = "ok"
            error_kind = None
            error_message = None
            text = extract_result.text
            metadata_json = json.dumps(asdict(extract_result.metadata))
        else:
            status = _status_for_extraction(extract_result.kind)
            error_kind = _error_kind_for_extraction(extract_result.kind)
            error_message = extract_result.message
            text = None
            metadata_json = "{}"
            expires_delta = self._config.error_cache_ttl
        return PageRecord(
            url_key=url.value,
            final_url=fetched.final_url.value,
            status=status,
            http_status=fetched.http_status.value,
            fetcher=fetched.fetcher,
            fetched_at=to_iso_utc(fetched.fetched_at),
            expires_at=to_iso_utc(compute_expires_at(now, expires_delta)),
            content_sha256=sha,
            html=fetched.body,
            text=text,
            text_format=page_format if text is not None else None,
            metadata_json=metadata_json,
            error_kind=error_kind,
            error_message=error_message,
        )

    def _failure_record(
        self, url: NormalizedUrl, failure: FetchFailure, now: datetime
    ) -> PageRecord:
        """Build the cache entry for a fetch failure (shorter TTL)."""
        expires_delta = (
            timedelta(seconds=failure.retry_after_s.value)
            if failure.retry_after_s is not None
            else self._config.error_cache_ttl
        )
        return PageRecord(
            url_key=url.value,
            final_url=failure.final_url.value if failure.final_url is not None else url.value,
            status=_status_for_failure(failure.kind),
            http_status=failure.http_status.value if failure.http_status is not None else None,
            fetcher="http",
            fetched_at=to_iso_utc(now),
            expires_at=to_iso_utc(compute_expires_at(now, expires_delta)),
            content_sha256=None,
            html=None,
            text=None,
            text_format=None,
            metadata_json="{}",
            error_kind=_error_kind_for_failure(failure.kind),
            error_message=failure.message,
        )

    def _content_changed_event(
        self, request: ServiceRequest, url: NormalizedUrl, now: datetime
    ) -> Event:
        """A journal-only event marking that the page body changed on re-fetch."""
        return Event(
            ts=to_iso_utc(now),
            agent=request.agent.value,
            task=request.task.value if request.task is not None else None,
            mcp_session_id=request.mcp_session_id,
            request_id=request.request_id,
            tool=request.tool,
            url=url.value,
            final_url=url.value,
            cache_hit=False,
            fresh=request.fresh,
            fetcher=None,
            status="ok",
            http_status=None,
            duration_ms=0,
            queue_ms=0,
            size_bytes=None,
            content_sha256=None,
            error_kind="content_changed",
        )

    async def _append_event(
        self,
        request: ServiceRequest,
        outcome: FetchOutcome,
        started: datetime,
        metrics: _RequestMetrics,
    ) -> None:
        """Write the journal entry for this request (inline, before returning)."""
        ended = self._clock.now()
        duration_ms = int((ended - started).total_seconds() * 1000)
        event = Event(
            ts=to_iso_utc(ended),
            agent=request.agent.value,
            task=request.task.value if request.task is not None else None,
            mcp_session_id=request.mcp_session_id,
            request_id=request.request_id,
            tool=request.tool,
            url=outcome.url,
            final_url=outcome.final_url,
            cache_hit=metrics.cache_hit,
            fresh=request.fresh,
            fetcher=outcome.fetcher,
            status=outcome.status,
            http_status=outcome.http_status,
            duration_ms=duration_ms,
            queue_ms=metrics.queue_ms,
            size_bytes=metrics.size_bytes,
            content_sha256=outcome.content_sha256,
            error_kind=outcome.error_kind,
        )
        await self._events.append(event)


def _status_for_failure(kind: FetchFailureKind) -> Status:
    """Map a fetch failure kind onto the response status."""
    match kind:
        case "blocked":
            return "blocked"
        case "not_found":
            return "not_found"
        case "timeout":
            return "timeout"
        case _:
            return "error"


_FAILURE_ERROR_KINDS: dict[FetchFailureKind, ErrorKind] = {
    "blocked": "blocked",
    "not_found": "not_found",
    "timeout": "timeout",
    "too_large": "too_large",
    "ssrf_blocked": "ssrf_blocked",
    "server_error": "server_error",
    "redirect_error": "redirect_error",
    "network": "internal",
}
"""Fetch failure kinds mapped onto journal/agent error kinds."""


def _error_kind_for_failure(kind: FetchFailureKind) -> ErrorKind:
    """Map a fetch failure kind onto the journal/agent error kind."""
    return _FAILURE_ERROR_KINDS[kind]


def _status_for_extraction(kind: ExtractionFailureKind) -> Status:
    """Empty or JS-only pages mean the static fetch was not enough."""
    match kind:
        case "needs_js" | "empty":
            return "needs_browser"
        case "parse_error":
            return "error"


def _error_kind_for_extraction(kind: ExtractionFailureKind) -> ErrorKind:
    match kind:
        case "needs_js" | "empty":
            return "needs_browser"
        case "parse_error":
            return "internal"


def _parse_metadata(raw: str) -> PageMetadata:
    """Parse stored metadata JSON; anything malformed degrades to empty."""
    data: object = json.loads(raw)
    if not isinstance(data, dict):
        return PageMetadata()
    return PageMetadata(
        title=_opt_str(data, "title"),
        author=_opt_str(data, "author"),
        published_at=_opt_str(data, "published_at"),
        language=_opt_str(data, "language"),
        publisher=_opt_str(data, "publisher"),
    )


def _opt_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None
