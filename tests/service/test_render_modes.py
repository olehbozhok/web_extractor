"""render=auto / render=browser pipeline behavior (S6)."""

from pathlib import Path

import pytest

from tests.service.conftest import ServiceHarness, _build_harness, service_request
from tests.service.service_helpers import fetched_ok, normalized
from web_extractor.domain.extract import Extracted, ExtractionFailure, PageMetadata
from web_extractor.domain.fetch import FetchFailure

_URL = "https://site.test/app"
_BODY = b"<html><body>app shell</body></html>"


@pytest.mark.asyncio
async def test_render_browser_without_fetcher_is_needs_browser(tmp_path: Path) -> None:
    """No browser fetcher configured: an explicit browser render says so."""
    harness = await _build_harness(tmp_path, with_browser=False)
    try:
        result = await harness.service.fetch(
            service_request(_URL, fresh=True, render="browser", page_format="text")
        )
    finally:
        await harness.database.close()
    assert result.status == "needs_browser"
    assert result.error_kind == "needs_browser"


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
