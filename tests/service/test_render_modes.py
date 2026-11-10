"""render=auto / render=browser pipeline behavior (S6)."""

import asyncio
from pathlib import Path

import pytest

from tests.service.conftest import ServiceHarness, _build_harness, service_request
from tests.service.service_helpers import fetched_ok, normalized
from web_extractor.domain.extract import Extracted, ExtractionFailure, PageMetadata
from web_extractor.domain.fetch import FetchFailure
from web_extractor.domain.types import HttpStatus

_URL = "https://site.test/app"
_BODY = b"<html><body>app shell</body></html>"


@pytest.mark.asyncio
async def test_render_browser_without_fetcher_is_browser_unavailable(tmp_path: Path) -> None:
    """No browser configured is a deployment fact, not a page that needs JS.

    It must not be cached either: the same URL succeeds once a browser is
    wired, and the static path stays untouched.
    """
    harness = await _build_harness(tmp_path, with_browser=False)
    try:
        first = await harness.service.fetch(
            service_request(_URL, render="browser", page_format="text")
        )
        harness.fetcher.script(fetched_ok(normalized(_URL)))
        harness.extractor.script(
            Extracted(text="static text", format="text", metadata=PageMetadata())
        )
        static = await harness.service.fetch(
            service_request(_URL, render="static", page_format="text")
        )
    finally:
        await harness.database.close()
    assert first.status == "error"
    assert first.error_kind == "browser_unavailable"
    assert first.fetcher == "browser"
    assert "not configured" in (first.error_message or "")
    assert static.status == "ok"


@pytest.mark.asyncio
async def test_render_browser_uses_browser_fetcher(browser_harness: ServiceHarness) -> None:
    browser_harness.browser_fetcher.script(
        fetched_ok(normalized(_URL), b"<html><body>rendered</body></html>")
    )
    browser_harness.extractor.script(
        Extracted(text="rendered", format="text", metadata=PageMetadata())
    )

    outcome = await browser_harness.service.fetch(
        service_request(_URL, fresh=True, render="browser", page_format="text")
    )

    assert outcome.status == "ok"
    assert outcome.text == "rendered"
    assert len(browser_harness.fetcher.calls) == 0  # the static fetcher is untouched
    assert len(browser_harness.browser_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_render_auto_retries_through_browser(browser_harness: ServiceHarness) -> None:
    """Static extraction finds a JS shell; the browser fetch returns content."""
    browser_harness.fetcher.script(fetched_ok(normalized(_URL), _BODY))
    browser_harness.extractor.script(ExtractionFailure("needs_js", "JS shell only"))
    browser_harness.browser_fetcher.script(
        fetched_ok(normalized(_URL), b"<html><body>rendered content</body></html>")
    )
    browser_harness.extractor.script(
        Extracted(text="rendered content", format="text", metadata=PageMetadata())
    )

    outcome = await browser_harness.service.fetch(
        service_request(_URL, render="auto", page_format="text")
    )

    assert outcome.status == "ok"
    assert outcome.text == "rendered content"
    assert len(browser_harness.fetcher.calls) == 1
    assert len(browser_harness.browser_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_render_auto_keeps_static_result_when_browser_down(
    browser_harness: ServiceHarness,
) -> None:
    """A browser outage must not turn into an infrastructure error (S6)."""
    browser_harness.browser_fetcher.script(
        FetchFailure("browser_unavailable", "browser service unreachable")
    )
    browser_harness.fetcher.script(fetched_ok(normalized(_URL), _BODY))
    browser_harness.extractor.script(ExtractionFailure("needs_js", "JS shell only"))

    outcome = await browser_harness.service.fetch(
        service_request(_URL, render="auto", page_format="text")
    )

    assert outcome.status == "needs_browser"
    assert outcome.error_kind == "needs_browser"


@pytest.mark.asyncio
async def test_render_auto_skips_browser_on_good_static(browser_harness: ServiceHarness) -> None:
    browser_harness.fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="real text", format="text", metadata=PageMetadata())
    )

    outcome = await browser_harness.service.fetch(
        service_request(_URL, render="auto", page_format="text")
    )

    assert outcome.status == "ok"
    assert len(browser_harness.browser_fetcher.calls) == 0


@pytest.mark.asyncio
async def test_browser_render_does_not_reuse_a_static_cache_entry(
    browser_harness: ServiceHarness,
) -> None:
    """A cached static result must not satisfy an explicit browser request."""
    browser_harness.fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="static text", format="text", metadata=PageMetadata())
    )
    await browser_harness.service.fetch(service_request(_URL, render="static", page_format="text"))
    browser_harness.browser_fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="rendered text", format="text", metadata=PageMetadata())
    )

    outcome = await browser_harness.service.fetch(
        service_request(_URL, render="browser", page_format="text")
    )

    assert outcome.text == "rendered text"
    assert len(browser_harness.browser_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_a_browser_cache_entry_serves_static_and_auto_requests(
    browser_harness: ServiceHarness,
) -> None:
    browser_harness.browser_fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="rendered text", format="text", metadata=PageMetadata())
    )
    await browser_harness.service.fetch(service_request(_URL, render="browser", page_format="text"))

    outcome = await browser_harness.service.fetch(
        service_request(_URL, render="auto", page_format="text")
    )

    assert outcome.text == "rendered text"
    assert len(browser_harness.fetcher.calls) == 0


def _browser_blocked() -> FetchFailure:
    return FetchFailure(
        "blocked",
        "403 through the browser",
        http_status=HttpStatus(403),
        final_url=normalized(_URL),
    )


@pytest.mark.asyncio
async def test_an_empty_render_from_the_browser_is_not_needs_browser(
    browser_harness: ServiceHarness,
) -> None:
    """The browser already rendered the page: needs_browser would tell the
    agent to retry with the fetcher it just used (docs/03, 5)."""
    browser_harness.browser_fetcher.script(
        fetched_ok(normalized(_URL), b"<html><body><canvas></canvas></body></html>")
    )
    browser_harness.extractor.script(ExtractionFailure("empty", "no text after rendering"))

    outcome = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    assert outcome.status == "error"
    assert outcome.error_kind == "empty_render"
    assert "no text after rendering" in (outcome.error_message or "")


@pytest.mark.asyncio
async def test_an_empty_render_error_is_cached_and_readable(
    browser_harness: ServiceHarness,
) -> None:
    """The cached empty_render record and its journal event must read back:
    the storage boundary knows every kind the pipeline can write. The cache
    is consulted before the TTL check and even on fresh re-fetches, so an
    unreadable record would make the URL permanently broken."""
    empty = b"<html><body><canvas></canvas></body></html>"
    browser_harness.browser_fetcher.script(
        fetched_ok(normalized(_URL), empty),
        fetched_ok(normalized(_URL), empty),
    )
    browser_harness.extractor.script(
        ExtractionFailure("empty", "no text after rendering"),
        ExtractionFailure("empty", "no text after rendering"),
    )

    first = await browser_harness.service.fetch(service_request(_URL, render="browser"))
    second = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    assert first.error_kind == "empty_render"
    assert second.status == "error"
    assert second.from_cache is True
    assert second.error_kind == "empty_render"
    rows = await browser_harness.event_rows()
    assert rows[-1]["error_kind"] == "empty_render"


@pytest.mark.asyncio
async def test_a_browser_failure_is_cached_for_browser_requests(
    browser_harness: ServiceHarness,
) -> None:
    """Re-asking a site that blocked the browser must not hit it again."""
    browser_harness.browser_fetcher.script(_browser_blocked())
    first = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    second = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    assert first.status == second.status == "blocked"
    assert second.from_cache is True
    assert second.fetcher == "browser"
    assert len(browser_harness.browser_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_a_browser_failure_does_not_answer_a_static_retry(
    browser_harness: ServiceHarness,
) -> None:
    """The advice after a browser failure is render=static; the cache must let it run."""
    browser_harness.browser_fetcher.script(_browser_blocked())
    await browser_harness.service.fetch(service_request(_URL, render="browser"))
    browser_harness.fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="static text", format="text", metadata=PageMetadata())
    )

    outcome = await browser_harness.service.fetch(service_request(_URL, render="static"))

    assert outcome.status == "ok"
    assert outcome.text == "static text"
    assert len(browser_harness.fetcher.calls) == 1


@pytest.mark.asyncio
async def test_a_static_success_overwrites_a_browser_failure_in_the_cache(
    browser_harness: ServiceHarness,
) -> None:
    """One record per URL (docs/03, cache and render): a static success
    replaces the browser failure, so the third, browser-mode request goes
    back to the site that still blocks the browser - a deliberate
    compromise over a composite cache key; this test pins the behavior."""
    browser_harness.browser_fetcher.script(_browser_blocked())
    first = await browser_harness.service.fetch(service_request(_URL, render="browser"))
    browser_harness.fetcher.script(fetched_ok(normalized(_URL)))
    browser_harness.extractor.script(
        Extracted(text="static text", format="text", metadata=PageMetadata())
    )
    second = await browser_harness.service.fetch(service_request(_URL, render="static"))
    browser_harness.browser_fetcher.script(_browser_blocked())
    third = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    assert first.status == "blocked"
    assert second.status == "ok"
    assert third.status == "blocked"
    assert third.from_cache is False
    assert len(browser_harness.browser_fetcher.calls) == 2


@pytest.mark.asyncio
async def test_a_browser_outage_is_not_cached(browser_harness: ServiceHarness) -> None:
    """browser_unavailable describes the deployment, not the page."""
    browser_harness.browser_fetcher.script(
        FetchFailure("browser_unavailable", "browser service unreachable"),
        fetched_ok(normalized(_URL)),
    )
    browser_harness.extractor.script(
        Extracted(text="rendered text", format="text", metadata=PageMetadata())
    )
    first = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    second = await browser_harness.service.fetch(service_request(_URL, render="browser"))

    assert first.error_kind == "browser_unavailable"
    assert second.status == "ok"
    assert len(browser_harness.browser_fetcher.calls) == 2


@pytest.mark.asyncio
async def test_failure_events_name_the_fetcher_that_failed(
    browser_harness: ServiceHarness,
) -> None:
    browser_harness.browser_fetcher.script(_browser_blocked())
    await browser_harness.service.fetch(service_request(_URL, render="browser"))

    rows = await browser_harness.event_rows()

    assert rows[-1]["fetcher"] == "browser"
    assert rows[-1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_browser_renders_respect_the_per_host_limit(browser_harness: ServiceHarness) -> None:
    """Browser fetches go through the same per-host cap (2 in the harness)."""
    urls = [f"https://site.test/page-{number}" for number in range(3)]
    browser = browser_harness.browser_fetcher
    browser.gated = True
    browser.script(*(fetched_ok(normalized(url)) for url in urls))
    browser_harness.extractor.script(
        *(Extracted(text="rendered", format="text", metadata=PageMetadata()) for _ in urls)
    )

    tasks = [
        asyncio.create_task(
            browser_harness.service.fetch(service_request(url, fresh=True, render="browser"))
        )
        for url in urls
    ]
    await browser.entered.wait()
    for _ in range(20):  # let every task run up to its first real wait
        await asyncio.sleep(0)
    in_browser_while_gated = len(browser.calls)
    browser.release.set()
    outcomes = await asyncio.gather(*tasks)

    assert in_browser_while_gated == 2
    assert [outcome.status for outcome in outcomes] == ["ok", "ok", "ok"]
    assert len(browser.calls) == 3
