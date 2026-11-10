"""Fetcher protocol and DNS resolver protocol.

Implementations (``http_fetcher``, ``browser_fetcher``) must return
``FetchResult`` — expected failures as values, never exceptions. Checks and
helpers both fetchers share (port allow-list, Retry-After parsing) live
here as well.
"""

from datetime import datetime
from email.utils import parsedate_to_datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from web_extractor.domain.fetch import FetchFailure, FetchRequest, FetchResult
from web_extractor.domain.types import PositiveInt
from web_extractor.domain.urls import NormalizedUrl

_RETRY_AFTER_MAX_SECONDS = 86400
"""Sanity cap for Retry-After so a hostile header cannot pin the cache long."""


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


def parse_retry_after(raw: str | None, now: datetime) -> PositiveInt | None:
    """Retry-After as seconds (integer form, then HTTP-date form).

    Shared by both fetchers; ``raw`` is the header value already looked up
    case-insensitively by the caller. The header is best-effort: any value
    that is not a sane positive duration is treated as absent (``None``).
    """
    if raw is None:
        return None
    stripped = raw.strip()
    seconds = _integer_seconds(stripped)
    if seconds is None:
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


def _integer_seconds(text: str) -> int | None:
    """The integer-form value of ``text``, or None when ``int()`` cannot read it.

    ``str.isdigit()`` alone lies: superscript digits (``'²'``) pass it while
    ``int()`` raises ValueError, and a digit run beyond CPython's 4300-digit
    int-parsing limit does the same. A hostile header must degrade to
    "absent", never crash the fetch.
    """
    if not (text.isascii() and text.isdigit()):
        return None
    try:
        return int(text)
    except ValueError:
        return None
