"""Fetcher protocol and DNS resolver protocol.

Implementations (``http_fetcher``, later ``browser_fetcher``) must return
``FetchResult`` — expected failures as values, never exceptions.
"""

from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from web_extractor.domain.fetch import FetchRequest, FetchResult


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
