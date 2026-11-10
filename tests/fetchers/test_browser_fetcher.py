"""BrowserFetcher tests: mapping /crawl answers per the verified contract."""

import json
from dataclasses import replace
from ipaddress import IPv4Address

import httpx
import pytest
import respx
from httpx import Response

from tests.fetchers.fetch_helpers import StaticResolver
from tests.service.service_helpers import FakeClock
from web_extractor.domain.fetch import Fetched, FetchFailure, FetchRequest
from web_extractor.domain.types import AgentId
from web_extractor.domain.urls import NormalizedUrl, parse_normalized_url
from web_extractor.fetchers.browser_fetcher import (
    BrowserFetcher,
    BrowserFetcherConfig,
    BrowserHealthProbe,
)

_URL = "https://site.test/page"


def _config() -> BrowserFetcherConfig:
    return BrowserFetcherConfig(
        base_url="http://crawl4ai.test:11235",
        token="stub-token-for-tests",
        page_timeout_ms=15000,
        max_concurrent=2,
        allowed_ports=(80, 443, 8080, 8443),
        max_response_bytes=10_000_000,
    )


def _fetcher() -> BrowserFetcher:
    # The final-URL re-check resolves hosts; no connection is ever made to
    # these addresses, so a public fake is fine for the success cases.
    resolver = StaticResolver(
        {
            "site.test": [IPv4Address("8.8.8.8")],
            "169.254.169.254": [IPv4Address("169.254.169.254")],
            "172.30.0.20": [IPv4Address("172.30.0.20")],
        }
    )
    return BrowserFetcher(_config(), FakeClock(), resolver)


def _request() -> FetchRequest:
    parsed = parse_normalized_url(_URL)
    assert isinstance(parsed, NormalizedUrl)
    return FetchRequest(url=parsed, fresh=False, render="browser", agent=AgentId("tester"))


def _crawl_payload(**overrides: object) -> dict[str, object]:
    result = {
        "html": "<html><body><p>rendered page</p></body></html>",
        "status_code": 200,
        "redirected_status_code": 200,
        "redirected_url": _URL,
        "response_headers": {"content-type": "text/html"},
        "success": False,  # the anti-bot heuristic marks real pages too
        "error_message": "Blocked by anti-bot protection: Structural: minimal_text",
    }
    result.update(overrides)
    return {"results": [result]}


def _answer_for(payload: dict[str, object]) -> Response:
    return Response(200, json=payload)


@pytest.mark.asyncio
@respx.mock
async def test_successful_render_ignores_anti_bot_flag() -> None:
    """A 200 answer with success=false is still a real page (spike 02, 5)."""
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(_crawl_payload()))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, Fetched)
    assert result.fetcher == "browser"
    assert b"rendered page" in result.body
    assert result.http_status.value == 200
    assert result.final_url.value == _URL


@respx.mock
@pytest.mark.asyncio
async def test_404_maps_to_not_found() -> None:
    payload = _crawl_payload(
        status_code=404, redirected_status_code=404, html="<html><body>404</body></html>"
    )
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(payload))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "not_found"


@respx.mock
@pytest.mark.asyncio
async def test_oversized_response_is_too_large() -> None:
    """The size limit applies to the browser like to the static fetcher
    (docs/01, 4.5): an arbitrary body must not reach cache and extraction."""
    fetcher = BrowserFetcher(
        replace(_config(), max_response_bytes=100),
        FakeClock(),
        StaticResolver({"site.test": [IPv4Address("8.8.8.8")]}),
    )
    respx.post("http://crawl4ai.test:11235/crawl").mock(
        return_value=_answer_for(_crawl_payload(html="x" * 101))
    )

    result = await fetcher.fetch(_request())

    assert isinstance(result, FetchFailure)
    assert result.kind == "too_large"
    assert "limit is 100" in result.message


@respx.mock
@pytest.mark.asyncio
async def test_403_maps_to_blocked() -> None:
    payload = _crawl_payload(status_code=403, redirected_status_code=403)
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(payload))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "blocked"


@respx.mock
@pytest.mark.asyncio
async def test_redirect_into_forbidden_network_is_ssrf_blocked() -> None:
    """302 -> final 403 with the egress proxy's URL blocked body (spike 02, 6)."""
    payload = _crawl_payload(
        status_code=302,
        redirected_status_code=403,
        redirected_url="http://172.30.0.20:8080/hit",
        html="URL blocked by internal policy",
    )
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(payload))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@respx.mock
@pytest.mark.asyncio
async def test_private_final_url_is_rechecked() -> None:
    """The core re-resolves the final URL and applies the SSRF rules."""
    payload = _crawl_payload(redirected_url="http://169.254.169.254/latest/meta-data/")
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(payload))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@respx.mock
@pytest.mark.asyncio
async def test_unreachable_service_is_browser_unavailable() -> None:
    respx.post("http://crawl4ai.test:11235/crawl").mock(side_effect=httpx.ConnectError("down"))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "browser_unavailable"


@respx.mock
@pytest.mark.asyncio
async def test_malformed_payload_is_browser_unavailable() -> None:
    respx.post("http://crawl4ai.test:11235/crawl").mock(
        return_value=Response(200, json={"results": []})
    )
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "browser_unavailable"


def _request_for(url: str) -> FetchRequest:
    parsed = parse_normalized_url(url)
    assert isinstance(parsed, NormalizedUrl)
    return FetchRequest(url=parsed, fresh=False, render="browser", agent=AgentId("tester"))


@respx.mock
@pytest.mark.asyncio
async def test_disallowed_port_is_ssrf_blocked_before_the_browser() -> None:
    """The browser path enforces the same port allow-list as the static one."""
    route = respx.post("http://crawl4ai.test:11235/crawl")
    result = await _fetcher().fetch(_request_for("https://site.test:6379/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"
    assert "6379" in result.message
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_private_start_url_is_ssrf_blocked_before_the_browser() -> None:
    route = respx.post("http://crawl4ai.test:11235/crawl")
    result = await _fetcher().fetch(_request_for("http://169.254.169.254/latest/meta-data/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_unresolvable_start_host_is_dns_before_the_browser() -> None:
    """Crawl4AI answers 400 'URL blocked' for such hosts; the core must say dns."""
    route = respx.post("http://crawl4ai.test:11235/crawl")
    result = await _fetcher().fetch(_request_for("https://no-such-host.test/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "dns"
    assert "no-such-host.test" in result.message
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_crawl4ai_url_blocked_400_is_ssrf_blocked() -> None:
    """Crawl4AI's own SSRF guard (e.g. after DNS rebinding) is not an outage."""
    respx.post("http://crawl4ai.test:11235/crawl").mock(
        return_value=Response(400, json={"detail": "URL blocked (SSRF protection): URL blocked"})
    )
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@respx.mock
@pytest.mark.asyncio
async def test_failed_navigation_is_network_with_browser_error_code() -> None:
    """A PDF or download URL fails navigation in Chromium (verified on 0.9.5)."""
    payload = _crawl_payload(
        html="",
        status_code=None,
        redirected_status_code=None,
        redirected_url=None,
        error_message=(
            "Unexpected error in _crawl_web at line 779:\nError: Failed on navigating "
            "ACS-GOTO:\nPage.goto: net::ERR_FAILED at https://site.test/page\nCall log:"
        ),
    )
    respx.post("http://crawl4ai.test:11235/crawl").mock(return_value=_answer_for(payload))
    result = await _fetcher().fetch(_request())
    assert isinstance(result, FetchFailure)
    assert result.kind == "network"
    assert "net::ERR_FAILED" in result.message
    assert "Call log" not in result.message


@respx.mock
@pytest.mark.asyncio
async def test_request_carries_token_and_bypass_config() -> None:
    route = respx.post("http://crawl4ai.test:11235/crawl").mock(
        return_value=_answer_for(_crawl_payload())
    )
    await _fetcher().fetch(_request())

    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["Authorization"] == "Bearer stub-token-for-tests"
    assert sent["crawler_config"]["params"]["cache_mode"] == "bypass"
    assert sent["crawler_config"]["params"]["process_iframes"] is True
    assert sent["browser_config"]["params"]["page_timeout"] == 15000


@pytest.mark.asyncio
async def test_health_probe_reports_instances() -> None:
    probe = BrowserHealthProbe(_config())
    with respx.mock:
        respx.get("http://crawl4ai.test:11235/health").mock(return_value=Response(200))
        respx.get("http://crawl4ai.test:11235/monitor/browsers").mock(
            return_value=Response(200, json={"browsers": [{"type": "permanent"}, {"type": "hot"}]})
        )
        health = await probe.probe()
    assert health.reachable is True
    assert health.chromium_instances == 2


@pytest.mark.asyncio
async def test_health_probe_degrades_on_unreachable() -> None:
    probe = BrowserHealthProbe(_config())
    with respx.mock:
        respx.get("http://crawl4ai.test:11235/health").mock(side_effect=httpx.ConnectError("down"))
        health = await probe.probe()
    assert health.reachable is False
    assert health.chromium_instances is None
