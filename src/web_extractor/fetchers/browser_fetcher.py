"""BrowserFetcher: a REST adapter over Crawl4AI (docs/02, section 7).

The response contract was verified live against crawl4ai 0.9.5 (spike 01
fields plus a stand probe): ``redirected_status_code`` carries the final
response's code, ``redirected_url`` the post-redirect URL, and the
``success`` flag is unreliable — Crawl4AI's unconditional anti-bot
heuristic marks real small responses (even 404/403 bodies) as
``success=false``. Status is therefore decided by
``redirected_status_code`` and the body only.

The core re-checks SSRF on the final URL (resolve + domain rules): the
browser's own egress proxy is the first line of defense, not the only
one (docs/spikes/02, conclusions 1-2).
"""

import asyncio
import json
from dataclasses import dataclass

import httpx

from web_extractor.domain.fetch import (
    Fetched,
    FetchFailure,
    FetchFailureKind,
    FetchRequest,
    FetchResult,
)
from web_extractor.domain.ssrf import SsrfViolation, check_address
from web_extractor.domain.types import (
    HTTP_BLOCKED_STATUSES,
    HTTP_NOT_FOUND_STATUSES,
    HTTP_OK_MAX,
    HTTP_OK_MIN,
    HTTP_REDIRECT_CODES,
    Clock,
    HttpStatus,
)
from web_extractor.domain.urls import NormalizedUrl, UrlParseError, parse_normalized_url
from web_extractor.fetchers.base import Resolver

_BROWSER_UNAVAILABLE_NOTE = "browser service unreachable"

_MAX_CONCURRENT_FLOOR = 1


@dataclass(frozen=True, slots=True)
class BrowserFetcherConfig:
    """Connection settings for the Crawl4AI service."""

    base_url: str
    """e.g. ``http://crawl4ai:11235``; the port is never published."""
    token: str
    """Bearer token; comes from the environment, never baked into images."""
    page_timeout_ms: int
    """Passed in every /crawl request (docs/01, 4.2)."""
    max_concurrent: int
    """Must be lower than Crawl4AI's pool.max_pages (docs/spikes/02)."""


@dataclass(frozen=True, slots=True)
class _CrawlAnswer:
    """The /crawl fields this adapter consumes, narrowed at the boundary."""

    html: str
    status_code: int | None
    redirected_status_code: int | None
    redirected_url: str | None
    response_headers: dict[str, str]


class BrowserFetcher:
    """Fetcher implementation rendering pages through the browser service."""

    def __init__(
        self,
        config: BrowserFetcherConfig,
        clock: Clock,
        resolver: Resolver,
    ) -> None:
        if config.max_concurrent < _MAX_CONCURRENT_FLOOR:
            msg = f"browser max_concurrent must be >= {_MAX_CONCURRENT_FLOOR}"
            raise ValueError(msg)
        self._config = config
        self._clock = clock
        self._resolver = resolver
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Render ``request.url`` in the browser; failures are values."""
        async with self._semaphore:
            return await self._fetch_locked(request)

    async def _fetch_locked(self, request: FetchRequest) -> FetchResult:
        body = {
            "urls": [request.url.value],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"text_mode": True, "page_timeout": self._config.page_timeout_ms},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass", "process_iframes": True},
            },
        }
        timeout = httpx.Timeout(
            connect=10.0,
            read=self._config.page_timeout_ms / 1000 + 30.0,
            write=10.0,
            pool=10.0,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(
                    f"{self._config.base_url}/crawl",
                    content=json.dumps(body),
                    headers={
                        "Authorization": f"Bearer {self._config.token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"{_BROWSER_UNAVAILABLE_NOTE} for {request.url}: {exc}"
            return FetchFailure("browser_unavailable", msg, final_url=request.url)
        answer = _parse_crawl_answer(payload)
        if answer is None:
            msg = f"{_BROWSER_UNAVAILABLE_NOTE}: malformed response for {request.url}"
            return FetchFailure("browser_unavailable", msg, final_url=request.url)
        try:
            return await self._to_result(request, answer)
        except OSError as exc:
            msg = f"final URL of {request.url} could not be resolved: {exc}"
            return FetchFailure("network", msg, final_url=request.url)

    async def _to_result(self, request: FetchRequest, answer: _CrawlAnswer) -> FetchResult:
        """Map the crawl answer onto domain types, SSRF-checking the final URL."""
        final_url = answer.redirected_url or request.url.value
        parsed = parse_normalized_url(final_url)
        if isinstance(parsed, UrlParseError):
            msg = f"browser returned a malformed final URL {final_url!r}: {parsed.message}"
            return FetchFailure("redirect_error", msg, final_url=request.url)
        violation = await self._check_final_url(parsed)
        if violation is not None:
            msg = f"{parsed} resolves to forbidden address (rule: {violation.rule})"
            return FetchFailure("ssrf_blocked", msg, final_url=request.url)

        code = answer.redirected_status_code or answer.status_code
        if code is None:
            msg = f"browser returned no status code for {request.url}"
            return FetchFailure("network", msg, final_url=parsed)
        status = HttpStatus(code)
        kind = _failure_kind_for(code, answer.html)
        if kind is not None:
            msg = f"{request.url} returned {code} through the browser ({kind})"
            return FetchFailure(kind, msg, http_status=status, final_url=parsed)
        return Fetched(
            final_url=parsed,
            http_status=status,
            headers=answer.response_headers,
            body=answer.html.encode("utf-8"),
            fetched_at=self._clock.now(),
            fetcher="browser",
        )

    async def _check_final_url(self, url: NormalizedUrl) -> SsrfViolation | None:
        """Resolve the final URL's host once and apply the SSRF rules."""
        try:
            addresses = await self._resolver.resolve(url.host)
        except OSError as exc:
            msg = f"final URL {url} did not resolve: {exc}"
            raise OSError(msg) from exc
        for address in addresses:
            violation = check_address(address)
            if violation is not None:
                return violation
        return None


def _failure_kind_for(code: int, html: str) -> FetchFailureKind | None:
    """Decide the failure kind from the final status code and body.

    A redirect into a forbidden network shows up as 302 with a final 403
    and the egress proxy's ``URL blocked`` body (docs/spikes/02, 6).
    """
    if code in HTTP_REDIRECT_CODES and "URL blocked" in html[:200]:
        return "ssrf_blocked"
    if code in HTTP_BLOCKED_STATUSES:
        return "blocked"
    if code in HTTP_NOT_FOUND_STATUSES:
        return "not_found"
    if code < HTTP_OK_MIN or code > HTTP_OK_MAX:
        return "server_error"
    return None


def _parse_crawl_answer(payload: object) -> _CrawlAnswer | None:
    """Narrow the /crawl JSON onto the fields the adapter consumes."""
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    html = _opt_str(first, "html")
    if html is None:
        return None
    headers_raw = first.get("response_headers")
    headers: dict[str, str] = {}
    if isinstance(headers_raw, dict):
        headers = {
            str(key): str(value)
            for key, value in headers_raw.items()
            if isinstance(value, (str, int))
        }
    return _CrawlAnswer(
        html=html,
        status_code=_opt_int(first, "status_code"),
        redirected_status_code=_opt_int(first, "redirected_status_code"),
        redirected_url=_opt_str(first, "redirected_url"),
        response_headers=headers,
    )


def _opt_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _opt_int(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class BrowserHealth:
    """Readiness snapshot of the browser service."""

    reachable: bool
    chromium_instances: int | None


class BrowserHealthProbe:
    """Readiness probe for /readyz: connectivity plus the browser pool size.

    Uses ``GET /monitor/browsers`` (a pool entry is one Chromium instance)
    and ``GET /health``; neither starts a crawl, so the probe is cheap —
    but note it still cannot detect a dead Chromium (docs/spikes/02, 3),
    which is what the container healthcheck is for.
    """

    def __init__(self, config: BrowserFetcherConfig) -> None:
        self._config = config

    async def probe(self) -> BrowserHealth:
        """Query the browser service; failures degrade to unreachable."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
                trust_env=False,
            ) as client:
                health = await client.get(
                    f"{self._config.base_url}/health",
                    headers=_auth(self._config.token),
                )
                health.raise_for_status()
                browsers = await client.get(
                    f"{self._config.base_url}/monitor/browsers",
                    headers=_auth(self._config.token),
                )
                browsers.raise_for_status()
                payload: object = browsers.json()
        except (httpx.HTTPError, ValueError):
            return BrowserHealth(reachable=False, chromium_instances=None)
        return BrowserHealth(reachable=True, chromium_instances=_count_browsers(payload))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count_browsers(payload: object) -> int | None:
    """Count pool entries; every entry is one Chromium instance."""
    if not isinstance(payload, dict):
        return None
    browsers = payload.get("browsers")
    if not isinstance(browsers, list):
        return None
    return len(browsers)
