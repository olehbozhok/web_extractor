"""Fetcher protocol and DNS resolver protocol.

Implementations (``http_fetcher``, ``browser_fetcher``) must return
``FetchResult`` — expected failures as values, never exceptions. Checks both
fetchers apply before any network access live here as well.
"""

from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from web_extractor.domain.fetch import FetchFailure, FetchRequest, FetchResult
from web_extractor.domain.urls import NormalizedUrl


class Fetcher(Protocol):
    """Downloads a page over the network according to a fetch request."""

    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Fetch ``request.url``; never raises for expected site failures."""
        ...  # pragma: no cover


class Resolver(Protocol):
    """Resolves a hostname to the addresses the fetcher may connect to.

    The fetcher checks every returned address with the SSRF rules and pins
    the first one, so resolution happens exactly once per hop (DNS-rebinding
    defense). Failures are raised as ``OSError`` (e.g. ``socket.gaierror``).
    """

    async def resolve(self, host: str) -> tuple[IPv4Address | IPv6Address, ...]:
        """All addresses of ``host``; non-empty or raises ``OSError``."""
        ...  # pragma: no cover


def port_failure(url: NormalizedUrl, allowed_ports: tuple[int, ...]) -> FetchFailure | None:
    """``ssrf_blocked`` for an explicit port outside the allow-list (SSRF, section 7).

    A URL without an explicit port uses its scheme's default and passes.
    """
    if url.port is not None and url.port not in allowed_ports:
        msg = f"port {url.port} of {url} is not in the allowed list {allowed_ports}"
        return FetchFailure("ssrf_blocked", msg, final_url=url)
    return None
