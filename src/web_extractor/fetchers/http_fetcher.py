"""Static HTTP fetcher with pinned-DNS SSRF protection.

Per hop the fetcher resolves the host once (``Resolver``), checks every
resolved address with the SSRF rules, and pins the first address onto a
custom httpcore network backend, so the TCP connection goes to exactly the
checked IP — DNS rebinding between check and connect cannot reach another
address. Redirects are followed manually to re-check every target; httpx
never follows them itself.
"""

import asyncio
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import urljoin

import httpcore
import httpx

from web_extractor.domain.fetch import Fetched, FetchFailure, FetchRequest, FetchResult
from web_extractor.domain.ssrf import SsrfViolation, check_address
from web_extractor.domain.types import (
    HTTP_BLOCKED_STATUSES,
    HTTP_NOT_FOUND_STATUSES,
    HTTP_OK_MAX,
    HTTP_OK_MIN,
    HTTP_TOO_MANY_REQUESTS,
    Clock,
    HttpStatus,
    PositiveInt,
)
from web_extractor.domain.urls import NormalizedUrl, UrlParseError, parse_normalized_url
from web_extractor.fetchers.base import Resolver

_CHUNK_SIZE = 65536
"""Streaming read size; keeps large responses under the size limit check."""

_RETRY_AFTER_MAX_SECONDS = 86400
"""Sanity cap for Retry-After so a hostile header cannot pin the cache long."""

type AddressChecker = Callable[[IPv4Address | IPv6Address], SsrfViolation | None]
"""SSRF decision over one address; production passes domain.check_address."""


@dataclass(frozen=True, slots=True)
class HttpFetcherConfig:
    """Settings for :class:`HttpFetcher`; validated by the composition root."""

    user_agent: str
    max_response_bytes: int
    connect_timeout_s: float
    read_timeout_s: float
    total_timeout_s: float
    max_redirects: int
    allowed_ports: tuple[int, ...] = (80, 443, 8080, 8443)


class SystemResolver:
    """Resolves DNS through the system resolver in a worker thread."""

    async def resolve(self, host: str) -> tuple[IPv4Address | IPv6Address, ...]:
        """All distinct stream addresses of ``host`` in resolver order."""
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        addresses: dict[str, IPv4Address | IPv6Address] = {}
        for family, _type, _proto, _canonname, sockaddr in records:
            ip_text = sockaddr[0]
            address = IPv4Address(ip_text) if family == socket.AF_INET else IPv6Address(ip_text)
            addresses[str(address)] = address
        if not addresses:
            msg = f"host {host!r} resolved to no addresses"
            raise OSError(msg)
        return tuple(addresses.values())


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connects only to exactly the address the fetcher checked.

    Any connection attempt for an unpinned host is refused (fail closed):
    httpcore should never ask for a host the fetcher has not resolved and
    checked first. Actual sockets are delegated to the standard anyio
    backend; the base ``AsyncNetworkBackend`` itself raises
    ``NotImplementedError`` on connect.
    """

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._pins: dict[str, IPv4Address | IPv6Address] = {}

    def pin(self, host: str, address: IPv4Address | IPv6Address) -> None:
        """Allow connections to ``host`` only via ``address``."""
        self._pins[host] = address

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: "Iterable[httpcore.SOCKET_OPTION] | None" = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned = self._pins.get(host)
        if pinned is None:
            msg = f"connection to {host!r}:{port} without a checked address"
            raise OSError(msg)
        return await self._inner.connect_tcp(
            str(pinned), port, timeout, local_address, socket_options
        )


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport over a pinned-DNS httpcore pool.

    ``httpx.AsyncHTTPTransport`` does not forward ``network_backend`` to
    httpcore (checked against httpx 0.28.1 source), so the pool is built
    here directly; the default (no-proxy) branch of the parent's ``__init__``
    is replicated and then replaced. ``trust_env=False`` everywhere: proxy
    environment variables would otherwise bypass the pinned connection.
    """

    def __init__(self, backend: PinnedNetworkBackend) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, cert=None, trust_env=False),
            http1=True,
            http2=False,
            network_backend=backend,
        )


class HttpFetcher:
    """Static fetcher: SSRF-checked, redirect-aware, size- and time-limited."""

    def __init__(
        self,
        config: HttpFetcherConfig,
        clock: Clock,
        resolver: Resolver,
        check: AddressChecker = check_address,
    ) -> None:
        """``check`` is the SSRF decision function; production uses the
        domain rules. Tests substitute a variant that allows the loopback
        test server while keeping every other restriction identical."""
        self._config = config
        self._clock = clock
        self._resolver = resolver
        self._check = check

    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Fetch ``request.url``; every expected failure comes back as a value."""
        port_failure = _port_failure(request.url, self._config.allowed_ports)
        if port_failure is not None:
            return port_failure
        backend = PinnedNetworkBackend()
        client = httpx.AsyncClient(
            transport=PinnedTransport(backend),
            headers={
                "User-Agent": self._config.user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=httpx.Timeout(
                connect=self._config.connect_timeout_s,
                read=self._config.read_timeout_s,
                write=self._config.connect_timeout_s,
                pool=self._config.connect_timeout_s,
            ),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            async with asyncio.timeout(self._config.total_timeout_s):
                return await self._fetch_with_redirects(client, backend, request.url)
        except TimeoutError:
            msg = f"fetching {request.url} exceeded total timeout {self._config.total_timeout_s}s"
            return FetchFailure("timeout", msg, final_url=request.url)
        except httpx.TimeoutException as exc:
            msg = f"stage timeout fetching {request.url}: {exc}"
            return FetchFailure("timeout", msg, final_url=request.url)
        except httpx.HTTPError as exc:
            msg = f"network error fetching {request.url}: {exc}"
            return FetchFailure("network", msg, final_url=request.url)
        except OSError as exc:
            msg = f"connection or resolution error fetching {request.url}: {exc}"
            return FetchFailure("network", msg, final_url=request.url)
        finally:
            await client.aclose()

    async def _fetch_with_redirects(
        self,
        client: httpx.AsyncClient,
        backend: PinnedNetworkBackend,
        start_url: NormalizedUrl,
    ) -> FetchResult:
        """Walk redirects manually, checking SSRF for every target."""
        url = start_url
        chain: list[NormalizedUrl] = []
        visited: set[str] = set()
        for _ in range(self._config.max_redirects + 1):
            if url.value in visited:
                msg = f"redirect cycle detected at {url}"
                return FetchFailure("redirect_error", msg, final_url=url)
            visited.add(url.value)
            pin_failure = await self._pin_host(backend, url)
            if pin_failure is not None:
                return pin_failure
            async with client.stream("GET", url.value) as response:
                if not response.is_redirect:
                    return await self._to_result(response, url, tuple(chain))
                step = self._redirect_step(url, response, chain, start_url)
                if isinstance(step, FetchFailure):
                    return step
                chain.append(url)
                url = step
        msg = f"more than {self._config.max_redirects} redirects from {start_url}"
        return FetchFailure("redirect_error", msg, final_url=url)

    def _redirect_step(
        self,
        url: NormalizedUrl,
        response: httpx.Response,
        chain: list[NormalizedUrl],
        start_url: NormalizedUrl,
    ) -> NormalizedUrl | FetchFailure:
        """Validate the next redirect hop: target, port, and chain length."""
        target = _redirect_target(url, response)
        if isinstance(target, FetchFailure):
            return target
        port_failure = _port_failure(target, self._config.allowed_ports)
        if port_failure is not None:
            return port_failure
        if len(chain) >= self._config.max_redirects:
            msg = f"more than {self._config.max_redirects} redirects from {start_url}"
            return FetchFailure("redirect_error", msg, final_url=url)
        return target

    async def _pin_host(
        self, backend: PinnedNetworkBackend, url: NormalizedUrl
    ) -> FetchFailure | None:
        """Resolve, SSRF-check all addresses, and pin the first one."""
        try:
            addresses = await self._resolver.resolve(url.host)
        except OSError as exc:
            msg = f"DNS resolution failed for {url.host!r}: {exc}"
            return FetchFailure("network", msg, final_url=url)
        for address in addresses:
            violation = self._check(address)
            if violation is not None:
                msg = f"{url} resolves to forbidden address {address} (rule: {violation.rule})"
                return FetchFailure("ssrf_blocked", msg, final_url=url)
        backend.pin(url.host, addresses[0])
        return None

    async def _to_result(
        self,
        response: httpx.Response,
        url: NormalizedUrl,
        chain: tuple[NormalizedUrl, ...],
    ) -> FetchResult:
        """Map a completed response onto Fetched or a FetchFailure value.

        Runs inside the ``client.stream`` context: the body must be read
        before the context closes.
        """
        status = HttpStatus(response.status_code)
        if status.value in HTTP_BLOCKED_STATUSES:
            retry_after = (
                _parse_retry_after(response.headers.get("retry-after"), self._clock.now())
                if status.value == HTTP_TOO_MANY_REQUESTS
                else None
            )
            msg = f"{url} returned {status.value} (blocked)"
            return FetchFailure(
                "blocked",
                msg,
                http_status=status,
                final_url=url,
                retry_after_s=retry_after,
            )
        if status.value in HTTP_NOT_FOUND_STATUSES:
            msg = f"{url} returned {status.value} (not found)"
            return FetchFailure("not_found", msg, http_status=status, final_url=url)
        if status.value < HTTP_OK_MIN or status.value > HTTP_OK_MAX:
            msg = f"{url} returned {status.value}"
            return FetchFailure("server_error", msg, http_status=status, final_url=url)

        declared = response.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > self._config.max_response_bytes
        ):
            msg = f"{url} body is {declared} bytes, limit is {self._config.max_response_bytes}"
            return FetchFailure("too_large", msg, http_status=status, final_url=url)

        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes(_CHUNK_SIZE):
            size += len(chunk)
            if size > self._config.max_response_bytes:
                msg = f"{url} body exceeds {self._config.max_response_bytes} bytes"
                return FetchFailure("too_large", msg, http_status=status, final_url=url)
            chunks.append(chunk)
        return Fetched(
            final_url=url,
            http_status=status,
            headers=dict(response.headers),
            body=b"".join(chunks),
            fetched_at=self._clock.now(),
            fetcher="http",
            redirect_chain=chain,
        )


def _port_failure(url: NormalizedUrl, allowed_ports: tuple[int, ...]) -> FetchFailure | None:
    """Reject ports outside the configured allow-list (SSRF section 7)."""
    if url.port is not None and url.port not in allowed_ports:
        msg = f"port {url.port} of {url} is not in the allowed list {allowed_ports}"
        return FetchFailure("ssrf_blocked", msg, final_url=url)
    return None


def _redirect_target(url: NormalizedUrl, response: httpx.Response) -> NormalizedUrl | FetchFailure:
    """Resolve the Location header against the current URL."""
    location = response.headers.get("location")
    if location is None:
        msg = f"{url} redirected without a Location header"
        return FetchFailure("redirect_error", msg, final_url=url)
    target = parse_normalized_url(urljoin(url.value, location))
    if isinstance(target, UrlParseError):
        msg = f"{url} redirected to invalid target {location!r}: {target.message}"
        return FetchFailure("redirect_error", msg, final_url=url)
    return target


def _parse_retry_after(raw: str | None, now: datetime) -> PositiveInt | None:
    """Retry-After as seconds (integer form, then HTTP-date form)."""
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped.isdigit():
        seconds = int(stripped)
    else:
        try:
            when = parsedate_to_datetime(stripped)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            return None
        seconds = int((when - now).total_seconds())
    if seconds <= 0 or seconds > _RETRY_AFTER_MAX_SECONDS:
        return None
    return PositiveInt(seconds)
