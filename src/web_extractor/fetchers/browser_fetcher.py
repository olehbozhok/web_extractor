"""BrowserFetcher: a REST adapter over Crawl4AI (docs/02, section 7).

The response contract was verified live against crawl4ai 0.9.5 (spike 01
fields plus a stand probe): ``redirected_status_code`` carries the final
response's code, ``redirected_url`` the post-redirect URL, and the
``success`` flag is unreliable — Crawl4AI's unconditional anti-bot
heuristic marks real small responses (even 404/403 bodies) as
``success=false``. Status is therefore decided by
``redirected_status_code`` and the body only.

Before calling Crawl4AI the core applies the same pre-flight rules as the
static fetcher (port allow-list, resolve, SSRF check of every address), so
an unresolvable or private start URL fails as ``dns``/``ssrf_blocked``
instead of Crawl4AI's opaque ``400 URL blocked``. After the render the core
re-checks SSRF on the final URL: the browser's own egress proxy is the first
line of defense, not the only one (docs/spikes/02, conclusions 1-2).
"""

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from web_extractor.domain.content_type import header_value
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
    HTTP_TOO_MANY_REQUESTS,
    Clock,
    HttpStatus,
)
from web_extractor.domain.urls import NormalizedUrl, UrlParseError, parse_normalized_url
from web_extractor.fetchers.base import Resolver, parse_retry_after, port_failure

_BROWSER_UNAVAILABLE_NOTE = "browser service unreachable"

_MAX_CONCURRENT_FLOOR = 1

_HTTP_BAD_REQUEST = 400
_SSRF_REJECTION_MARKER = "URL blocked"
"""Crawl4AI's own SSRF guard answers 400 with this text in ``detail``."""

_CHROMIUM_NET_ERROR = re.compile(r"net::ERR_[A-Z0-9_]+")
"""Chromium's navigation error code inside Crawl4AI's ``error_message``."""

_MAX_ERROR_EXCERPT = 200


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
    allowed_ports: tuple[int, ...]
    """The same port allow-list as the static fetcher (SSRF, section 7)."""
    max_response_bytes: int
    """Bodies above this many UTF-8 bytes come back ``too_large`` like the
    static fetcher's (docs/01, 4.5), instead of flowing into the cache and
    extraction."""


@dataclass(frozen=True, slots=True)
class _CrawlAnswer:
    """The /crawl fields this adapter consumes, narrowed at the boundary."""

    html: str
    status_code: int | None
    redirected_status_code: int | None
    redirected_url: str | None
    response_headers: dict[str, str]
    error_message: str | None
    """Crawl4AI's failure text; carries Chromium's ``net::ERR_*`` code."""


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
        """Render ``request.url`` in the browser; failures are values.

        Expected failures: ``ssrf_blocked`` and ``dns`` from the pre-flight
        checks (no browser call is made), ``blocked``/``not_found``/
        ``server_error`` from the final status, ``network`` when Chromium
        could not load the URL (e.g. a PDF download), ``browser_unavailable``
        when Crawl4AI itself is unreachable or answers garbage.
        """
        preflight = await self._preflight(request.url)
        if preflight is not None:
            return preflight
        async with self._semaphore:
            return await self._fetch_locked(request)

    async def _preflight(self, url: NormalizedUrl) -> FetchFailure | None:
        """Port allow-list, resolution and SSRF rules for the start URL."""
        bad_port = port_failure(url, self._config.allowed_ports)
        if bad_port is not None:
            return bad_port
        try:
            violation = await self._check_host(url)
        except OSError as exc:
            msg = f"DNS resolution failed for {url.host!r}: {exc}"
            return FetchFailure("dns", msg, final_url=url)
        if violation is not None:
            msg = f"{url} resolves to forbidden address (rule: {violation.rule})"
            return FetchFailure("ssrf_blocked", msg, final_url=url)
        return None

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
                if _is_ssrf_rejection(response):
                    msg = f"browser service refused {request.url} as a forbidden address"
                    return FetchFailure("ssrf_blocked", msg, final_url=request.url)
                response.raise_for_status()
                payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"{_BROWSER_UNAVAILABLE_NOTE} for {request.url}: {exc}"
            return FetchFailure("browser_unavailable", msg, final_url=request.url)
        answer = _parse_crawl_answer(payload)
        if answer is None:
            msg = f"{_BROWSER_UNAVAILABLE_NOTE}: malformed response for {request.url}"
            return FetchFailure("browser_unavailable", msg, final_url=request.url)
        return await self._to_result(request, answer)

    async def _to_result(self, request: FetchRequest, answer: _CrawlAnswer) -> FetchResult:
        """Map the crawl answer onto domain types, SSRF-checking the final URL."""
        final_url = answer.redirected_url or request.url.value
        parsed = parse_normalized_url(final_url)
        if isinstance(parsed, UrlParseError):
            msg = f"browser returned a malformed final URL {final_url!r}: {parsed.message}"
            return FetchFailure("redirect_error", msg, final_url=request.url)
        bad_port = port_failure(parsed, self._config.allowed_ports)
        if bad_port is not None:
            return bad_port
        try:
            violation = await self._check_host(parsed)
        except OSError as exc:
            # The final host may exist only in the browser's network (spike 02,
            # 3); the proxy body then names the block more truthfully than the
            # resolution failure would.
            if _redirect_into_blocked_network(answer.status_code, answer.html):
                msg = f"{request.url} was redirected by the browser into a forbidden network"
                return FetchFailure("ssrf_blocked", msg, final_url=parsed)
            msg = f"final URL of {request.url} did not resolve: {exc}"
            return FetchFailure("dns", msg, final_url=parsed)
        if violation is not None:
            msg = f"{parsed} resolves to forbidden address (rule: {violation.rule})"
            return FetchFailure("ssrf_blocked", msg, final_url=parsed)
        return self._result_for_status(request, answer, parsed)

    def _result_for_status(
        self, request: FetchRequest, answer: _CrawlAnswer, parsed: NormalizedUrl
    ) -> FetchResult:
        """The failure or success mapped from the answer's final status."""
        code = answer.redirected_status_code or answer.status_code
        if code is None:
            reason = _navigation_error(answer.error_message)
            msg = f"browser could not load {request.url}: {reason}"
            return FetchFailure("network", msg, final_url=parsed)
        status = HttpStatus(code)
        kind = _failure_kind_for(answer.status_code, code, answer.html)
        if kind is not None:
            msg = f"{request.url} returned {code} through the browser ({kind})"
            retry_after = (
                parse_retry_after(
                    header_value(answer.response_headers, "retry-after"), self._clock.now()
                )
                if kind == "blocked" and status.value == HTTP_TOO_MANY_REQUESTS
                else None
            )
            return FetchFailure(
                kind, msg, http_status=status, final_url=parsed, retry_after_s=retry_after
            )
        body = answer.html.encode("utf-8")
        if len(body) > self._config.max_response_bytes:
            msg = (
                f"body of {request.url} through the browser is {len(body)} bytes, "
                f"limit is {self._config.max_response_bytes}"
            )
            return FetchFailure("too_large", msg, http_status=status, final_url=parsed)
        return Fetched(
            final_url=parsed,
            http_status=status,
            headers=answer.response_headers,
            body=body,
            fetched_at=self._clock.now(),
            fetcher="browser",
        )

    async def _check_host(self, url: NormalizedUrl) -> SsrfViolation | None:
        """Resolve the URL's host once and apply the SSRF rules to every address.

        Resolution failures propagate as ``OSError`` from the resolver; the
        callers name the URL in their own messages.
        """
        addresses = await self._resolver.resolve(url.host)
        for address in addresses:
            violation = check_address(address)
            if violation is not None:
                return violation
        return None


def _is_ssrf_rejection(response: httpx.Response) -> bool:
    """Crawl4AI's own SSRF guard: ``400 {"detail": "URL blocked ..."}``."""
    return (
        response.status_code == _HTTP_BAD_REQUEST
        and _SSRF_REJECTION_MARKER in response.text[:_MAX_ERROR_EXCERPT]
    )


def _navigation_error(error_message: str | None) -> str:
    """Chromium's ``net::ERR_*`` code, else the first line of Crawl4AI's text."""
    if error_message is None or not error_message.strip():
        return "no status code and no error message"
    found = _CHROMIUM_NET_ERROR.search(error_message)
    if found is not None:
        return found.group(0)
    return error_message.strip().splitlines()[0][:_MAX_ERROR_EXCERPT]


def _redirect_into_blocked_network(first_code: int | None, html: str) -> bool:
    """The egress proxy's signature of a redirect into a forbidden network
    (docs/spikes/02, 6): the FIRST status is a redirect and the body is its
    ``URL blocked`` page. The final code alone - a genuine 403 carrying the
    same phrase - is an ordinary block, not a forbidden network."""
    return first_code in HTTP_REDIRECT_CODES and _SSRF_REJECTION_MARKER in html[:200]


def _failure_kind_for(
    first_code: int | None, final_code: int, html: str
) -> FetchFailureKind | None:
    """Decide the failure kind from the first and final status codes and body.

    A redirect into a forbidden network shows up as a redirect status with
    the egress proxy's ``URL blocked`` body (docs/spikes/02, 6): the final
    code alone (403) cannot be told apart from an anti-bot block.
    """
    if _redirect_into_blocked_network(first_code, html):
        return "ssrf_blocked"
    if final_code in HTTP_BLOCKED_STATUSES:
        return "blocked"
    if final_code in HTTP_NOT_FOUND_STATUSES:
        return "not_found"
    if final_code < HTTP_OK_MIN or final_code > HTTP_OK_MAX:
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
        error_message=_opt_str(first, "error_message"),
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
