"""URL parsing and normalization.

``parse_normalized_url`` is the only way to obtain a ``NormalizedUrl``: it
turns untrusted input into a URL that is safe to fetch and stable to cache
against. Normalization (host case, IDN, default ports, fragment removal,
query param ordering) is documented in the parser's docstring; the same
normalization defines the cache key, so it must never change without a
migration of cached entries.
"""

import fnmatch
import re
from collections.abc import Collection
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

MAX_URL_LENGTH = 2048
"""Reject absurdly long URLs early; they are pathological, not pages."""

MAX_HOST_LENGTH = 253
"""Maximum total hostname length (RFC 1035)."""

MAX_LABEL_LENGTH = 63
"""Maximum single hostname label length (RFC 1035)."""

_HOSTNAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\Z")
"""Accepted host charset after IDN encoding: letters, digits, dots, dashes.
The stdlib IDNA codec does not check pure-ASCII labels, so this regex is
what rejects spaces, underscores and the like."""

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_DEFAULT_PORTS = {"http": 80, "https": 443}


class NormalizedUrl:
    """An absolute ``http``/``https`` URL, normalized for fetching and caching.

    Instances can only be created by :func:`parse_normalized_url`; direct
    construction raises :class:`TypeError`. The stored form is guaranteed to
    have: lowercase scheme and host, no credentials, no fragment, no default
    port, a non-empty path, normalized IDN (punycode), and query parameters
    filtered and sorted.
    """

    __slots__ = ("_host", "_port", "_scheme", "_value")

    _value: str
    _host: str
    _scheme: str
    _port: int | None

    def __init__(self, _value: str) -> None:
        # The parameter is accepted (so calls look natural) but always
        # rejected: only parse_normalized_url may create instances.
        msg = (
            "NormalizedUrl can only be created by web_extractor.domain.urls.parse_normalized_url()"
        )
        raise TypeError(msg)

    @classmethod
    def _create(cls, value: str, *, host: str, scheme: str, port: int | None) -> "NormalizedUrl":
        instance = object.__new__(cls)
        instance._value = value
        instance._host = host
        instance._scheme = scheme
        instance._port = port
        return instance

    @property
    def value(self) -> str:
        """The normalized URL string; also the cache key."""
        return self._value

    @property
    def host(self) -> str:
        """The normalized (punycode, lowercase) host, without brackets."""
        return self._host

    @property
    def scheme(self) -> str:
        """The lowercase scheme; always ``http`` or ``https``."""
        return self._scheme

    @property
    def port(self) -> int | None:
        """The explicit port, or None when it is the scheme's default."""
        return self._port

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NormalizedUrl):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"NormalizedUrl({self._value!r})"

    def __str__(self) -> str:
        return self._value


type UrlFailureReason = Literal[
    "empty",
    "too_long",
    "no_scheme",
    "unsupported_scheme",
    "has_credentials",
    "missing_host",
    "invalid_host",
    "invalid_port",
]
"""Machine-readable reason a raw URL string was rejected."""


@dataclass(frozen=True, slots=True)
class UrlParseError:
    """Expected failure of URL parsing; carrying it, not raising, keeps the
    parser a pure function and forces callers to handle rejection."""

    reason: UrlFailureReason
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            msg = "UrlParseError.message must not be empty"
            raise ValueError(msg)


def parse_normalized_url(
    raw: str, tracking_params: Collection[str] = frozenset()
) -> NormalizedUrl | UrlParseError:
    """Parse a raw URL string into a ``NormalizedUrl``.

    Normalization applied (deterministic; doubles as the cache key rule):

    - surrounding whitespace stripped, scheme and host lowercased;
    - IDN hosts converted to punycode (IDNA 2003 semantics of the stdlib);
    - ``user:pass@`` credentials rejected, not stripped;
    - fragment dropped (it is never sent to servers);
    - explicit default port (80 for http, 443 for https) dropped;
    - empty path replaced with ``/``;
    - query re-encoded through ``parse_qsl``/``urlencode``: parameters matching
      a tracking pattern (exact name or shell-style pattern like ``utm_*``)
      dropped, the rest sorted by (name, value). Sorting assumes query
      parameters are semantically unordered, which holds for essentially all
      public pages;
    - length capped at ``MAX_URL_LENGTH``.
    """
    candidate = raw.strip()
    error = _length_error(candidate)
    if error is None:
        error = _scheme_error(candidate)
    if error is None:
        error = _netloc_error(candidate)
    if error is not None:
        return error

    parts = urlsplit(candidate)  # already validated above: cannot fail
    scheme = parts.scheme.lower()
    hostname = parts.hostname
    host = _normalize_host(hostname) if hostname else None
    if host is None:
        msg = f"host {hostname!r} is not a valid IP literal or IDN hostname"
        return UrlParseError("invalid_host", msg)

    port = parts.port
    path = parts.path or "/"
    query = _normalized_query(parts.query, tracking_params)
    host_for_url = f"[{host}]" if ":" in host else host
    port_part = "" if port is None or port == _DEFAULT_PORTS[scheme] else f":{port}"
    value = f"{scheme}://{host_for_url}{port_part}{path}{query}"
    return NormalizedUrl._create(value, host=host, scheme=scheme, port=port)


def _length_error(candidate: str) -> UrlParseError | None:
    """Reject empty input and URLs beyond ``MAX_URL_LENGTH``."""
    if not candidate:
        return UrlParseError("empty", "URL is empty")
    if len(candidate) > MAX_URL_LENGTH:
        msg = f"URL is longer than {MAX_URL_LENGTH} characters"
        return UrlParseError("too_long", msg)
    return None


def _scheme_error(candidate: str) -> UrlParseError | None:
    """Reject URLs whose scheme is missing or not http/https."""
    try:
        parts = urlsplit(candidate)
    except ValueError:
        msg = "URL does not parse (malformed IPv6 literal or control characters)"
        return UrlParseError("invalid_host", msg)
    scheme = parts.scheme.lower()
    if not scheme:
        return UrlParseError("no_scheme", "URL has no scheme")
    if scheme not in _ALLOWED_SCHEMES:
        msg = f"scheme {scheme!r} is not allowed, only http and https"
        return UrlParseError("unsupported_scheme", msg)
    return None


def _netloc_error(candidate: str) -> UrlParseError | None:
    """Reject embedded credentials, malformed ports and missing hosts."""
    parts = urlsplit(candidate)
    if "@" in parts.netloc:
        return UrlParseError("has_credentials", "URLs with embedded credentials are not allowed")
    try:
        # Accessing .port validates it; only the ValueError matters here.
        _ = parts.port
    except ValueError:
        msg = "URL has a malformed port"
        return UrlParseError("invalid_port", msg)
    if not parts.hostname:
        return UrlParseError("missing_host", "URL has no host")
    return None


def _normalize_host(hostname: str) -> str | None:
    """Lowercase an IP literal or convert a hostname to punycode.

    Returns None when the host is neither an IP literal nor a syntactically
    valid hostname.
    """
    candidate = hostname.rstrip(".")
    if not candidate:
        return None
    try:
        ip_address(candidate)
    except ValueError:
        pass
    else:
        return candidate.lower()
    try:
        encoded = candidate.lower().encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _HOSTNAME_RE.fullmatch(encoded) or len(encoded) > MAX_HOST_LENGTH:
        return None
    if any(len(label) > MAX_LABEL_LENGTH for label in encoded.split(".")):
        return None
    return encoded


def _normalized_query(raw_query: str, tracking_params: Collection[str]) -> str:
    """Return the normalized query string starting with ``?``, or ``""``."""
    if not raw_query:
        return ""
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    kept = sorted((name, value) for name, value in pairs if not _is_tracking(name, tracking_params))
    if not kept:
        return ""
    return f"?{urlencode(kept)}"


def _is_tracking(name: str, patterns: Collection[str]) -> bool:
    """True when a query parameter name matches a tracking pattern.

    A pattern is an exact name or a shell-style wildcard, e.g. ``utm_*``.
    """
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
