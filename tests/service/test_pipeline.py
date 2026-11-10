"""Pipeline tests: scenarios S1-S5, S8 and the journal (01_requirements, 3)."""

import asyncio

import pytest

from tests.service.conftest import ServiceHarness, service_request
from tests.service.service_helpers import extracted, fetched_blocked, fetched_ok, normalized, sha_of
from web_extractor.domain.extract import Extracted, ExtractionFailure, PageMetadata
from web_extractor.domain.fetch import FetchFailure
from web_extractor.domain.listing_links import LinkItem
from web_extractor.domain.ttl import parse_iso_utc
from web_extractor.domain.types import HttpStatus, PositiveInt
from web_extractor.storage.cache_repository import CacheRepository

_URL = "https://site.test/page"
_BODY_1 = b"<html><body>v1</body></html>"
_BODY_2 = b"<html><body>v2</body></html>"
_LISTING_BODY = (
    b'<html><body><a href="/a">First headline</a><a href="/b">Second headline</a></body></html>'
)
_PLAIN_BODY = b"<html><body><p>Just a paragraph, no anchors here.</p></body></html>"


@pytest.mark.asyncio
async def test_s1_miss_fetches_extracts_stores(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted())

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "ok"
    assert outcome.text == "Main page text."
    assert outcome.from_cache is False
    assert outcome.http_status == 200
    assert outcome.final_url == _URL
    assert outcome.content_sha256 == sha_of(_BODY_1)
    assert len(harness.fetcher.calls) == 1
    events = await harness.event_rows()
    assert len(events) == 1
    assert events[0]["status"] == "ok"
    assert events[0]["cache_hit"] == 0


@pytest.mark.asyncio
async def test_s2_hit_served_from_cache(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted())

    first = await harness.service.fetch(service_request(_URL))
    second = await harness.service.fetch(service_request(_URL, request_id="req-2"))

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.text == first.text
    assert len(harness.fetcher.calls) == 1  # no second network fetch
    events = await harness.event_rows()
    assert [row["cache_hit"] for row in events] == [0, 1]


@pytest.mark.asyncio
async def test_s3_five_concurrent_requests_one_fetch(harness: ServiceHarness) -> None:
    harness.fetcher.gated = True
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted())

    runner = asyncio.create_task(harness.service.fetch(service_request(_URL, request_id="req-1")))
    await harness.fetcher.entered.wait()
    waiters = [
        asyncio.create_task(harness.service.fetch(service_request(_URL, request_id=f"req-{n}")))
        for n in range(2, 6)
    ]
    harness.fetcher.release.set()
    outcomes = await asyncio.gather(runner, *waiters)

    assert len({outcome.text for outcome in outcomes}) == 1
    assert len(harness.fetcher.calls) == 1
    events = await harness.event_rows()
    # The runner fetched; the other four piggybacked (single-flight joiners
    # or, if the shared task finished first, cache readers).
    assert [row["cache_hit"] for row in events] == [0, 1, 1, 1, 1]


@pytest.mark.asyncio
async def test_s4_fresh_bypasses_cache_and_records_content_change(
    harness: ServiceHarness,
) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted("old body text"))
    await harness.service.fetch(service_request(_URL))

    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_2))
    harness.extractor.script(extracted("new body text"))
    fresh = await harness.service.fetch(service_request(_URL, fresh=True, request_id="req-2"))

    assert fresh.from_cache is False
    assert fresh.text == "new body text"
    assert fresh.content_sha256 == sha_of(_BODY_2)
    assert len(harness.fetcher.calls) == 2
    events = await harness.event_rows()
    content_changed = [row for row in events if row["error_kind"] == "content_changed"]
    assert len(content_changed) == 1


@pytest.mark.asyncio
async def test_s5_blocked_is_value_and_cached(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_blocked(normalized(_URL)))

    first = await harness.service.fetch(service_request(_URL))
    second = await harness.service.fetch(service_request(_URL, request_id="req-2"))

    assert first.status == "blocked"
    assert first.error_kind == "blocked"
    assert first.http_status == 403
    assert second.status == "blocked"
    assert second.from_cache is True
    assert len(harness.fetcher.calls) == 1  # blocked site not hit again


@pytest.mark.asyncio
async def test_s8_ssrf_blocked_is_error_with_event(harness: ServiceHarness) -> None:
    harness.fetcher.script(
        FetchFailure("ssrf_blocked", "forbidden network 10.0.0.1", final_url=normalized(_URL))
    )

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "error"
    assert outcome.error_kind == "ssrf_blocked"
    events = await harness.event_rows()
    assert events[0]["error_kind"] == "ssrf_blocked"


@pytest.mark.asyncio
async def test_invalid_url_rejected_with_event(harness: ServiceHarness) -> None:
    outcome = await harness.service.fetch(service_request("not-a-url"))

    assert outcome.status == "error"
    assert outcome.error_kind == "invalid_argument"
    assert len(harness.fetcher.calls) == 0
    events = await harness.event_rows()
    assert events[0]["error_kind"] == "invalid_argument"
    assert events[0]["status"] == "error"


@pytest.mark.asyncio
async def test_needs_js_extraction_becomes_needs_browser(harness: ServiceHarness) -> None:

    harness.fetcher.script(fetched_ok(normalized(_URL)))
    harness.extractor.script(ExtractionFailure("needs_js", "JS required"))

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "needs_browser"
    assert outcome.error_kind == "needs_browser"
    assert outcome.text is None
    events = await harness.event_rows()
    assert events[0]["status"] == "needs_browser"


@pytest.mark.asyncio
async def test_retry_after_extends_cache_lifetime(harness: ServiceHarness) -> None:
    failure = FetchFailure(
        "blocked",
        "429 too many requests",
        http_status=HttpStatus(429),
        final_url=normalized(_URL),
        retry_after_s=PositiveInt(3600),
    )
    harness.fetcher.script(failure)

    await harness.service.fetch(service_request(_URL))

    record = await CacheRepository(harness.database).get(_URL)
    assert record is not None
    assert record.status == "blocked"
    # Retry-After (1h) outlives the default error TTL (1h exactly); the entry
    # must not expire earlier than the promised moment.
    expires_at = parse_iso_utc(record.expires_at)
    assert expires_at >= harness.clock.now()


@pytest.mark.asyncio
async def test_ttl_expiry_triggers_refetch(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted())
    await harness.service.fetch(service_request(_URL))

    harness.clock.advance(24 * 3600 + 1)  # default TTL is 24 hours
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_2))
    harness.extractor.script(extracted("updated"))
    second = await harness.service.fetch(service_request(_URL, request_id="req-2"))

    assert second.from_cache is False
    assert second.text == "updated"
    assert len(harness.fetcher.calls) == 2


@pytest.mark.asyncio
async def test_format_switch_reextracts_from_cached_html(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted("plain text"))
    await harness.service.fetch(service_request(_URL, page_format="text"))

    harness.extractor.script(
        Extracted(text="# Markdown text", format="markdown", metadata=PageMetadata())
    )
    second = await harness.service.fetch(
        service_request(_URL, page_format="markdown", request_id="req-2")
    )

    assert second.from_cache is True
    assert second.text == "# Markdown text"
    assert second.text_format == "markdown"
    assert len(harness.fetcher.calls) == 1  # HTML came from the cache
    assert len(harness.extractor.calls) == 2


@pytest.mark.asyncio
async def test_event_row_fields_are_complete(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted())

    await harness.service.fetch(service_request(_URL, task="verify-citations", request_id="req-42"))

    events = await harness.event_rows()
    row = events[0]
    assert row["agent"] == "tester"
    assert row["task"] == "verify-citations"
    assert row["request_id"] == "req-42"
    assert row["tool"] == "fetch_page"
    assert row["url"] == _URL
    assert row["fresh"] == 0
    assert row["fetcher"] == "http"
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
    assert isinstance(row["queue_ms"], int) and row["queue_ms"] >= 0
    assert row["content_sha256"] == sha_of(_BODY_1)
    assert row["bytes"] == len(_BODY_1)


@pytest.mark.asyncio
async def test_concurrent_requests_for_different_formats_each_get_their_own(
    harness: ServiceHarness,
) -> None:
    """A joiner of another format must not receive the leader's text unlabeled-wrong."""
    harness.fetcher.gated = True
    harness.fetcher.script(fetched_ok(normalized(_URL), _BODY_1))
    harness.extractor.script(extracted("plain text"))  # the leader's extraction
    harness.extractor.script(extracted("**markdown text**", page_format="markdown"))  # re-extract

    leader = asyncio.create_task(
        harness.service.fetch(service_request(_URL, page_format="text", request_id="req-1"))
    )
    await harness.fetcher.entered.wait()
    joiner = asyncio.create_task(
        harness.service.fetch(service_request(_URL, page_format="markdown", request_id="req-2"))
    )
    harness.fetcher.release.set()
    leader_outcome, joiner_outcome = await asyncio.gather(leader, joiner)

    assert len(harness.fetcher.calls) == 1  # still one network fetch
    assert (leader_outcome.text, leader_outcome.text_format) == ("plain text", "text")
    assert (joiner_outcome.text, joiner_outcome.text_format) == (
        "**markdown text**",
        "markdown",
    )


@pytest.mark.asyncio
async def test_ok_html_outcome_carries_listing_links(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _LISTING_BODY))
    harness.extractor.script(extracted())

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "ok"
    assert outcome.listing_links == (
        LinkItem(name="First headline", link="https://site.test/a"),
        LinkItem(name="Second headline", link="https://site.test/b"),
    )


@pytest.mark.asyncio
async def test_html_without_anchors_yields_empty_listing_links(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), _PLAIN_BODY))
    harness.extractor.script(extracted())

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "ok"
    assert outcome.listing_links == ()


@pytest.mark.asyncio
async def test_pdf_outcome_has_no_listing_links(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_ok(normalized(_URL), b"%PDF-1.4 fake body"))
    harness.extractor.script(extracted())

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "ok"
    assert outcome.document == "pdf"
    assert outcome.listing_links is None


@pytest.mark.asyncio
async def test_error_outcome_has_no_listing_links(harness: ServiceHarness) -> None:
    harness.fetcher.script(fetched_blocked(normalized(_URL)))

    outcome = await harness.service.fetch(service_request(_URL))

    assert outcome.status == "blocked"
    assert outcome.listing_links is None
