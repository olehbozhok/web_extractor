"""Core domain value types.

Every type here is immutable and validates itself in ``__post_init__``: an
instance cannot exist in an invalid state. System boundaries (MCP arguments,
HTTP, database rows, config) convert raw input into these types through the
parsers in this package (``parse_normalized_url`` and friends); code further
in must never re-check what a type already guarantees (AGENTS.md section 5).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

type Status = Literal["ok", "blocked", "needs_browser", "not_found", "timeout", "error"]
"""Outcome of a pipeline run as reported to agents and stored in ``events``."""

type ErrorKind = Literal[
    "invalid_argument",
    "ssrf_blocked",
    "blocked",
    "not_found",
    "timeout",
    "needs_browser",
    "too_large",
    "dns",
    "network",
    "unsupported_type",
    "browser_unavailable",
    "unknown_cursor",
    "stale_cursor",
    "internal",
    "server_error",
    "redirect_error",
    "content_changed",
]
"""Machine-readable kind of an expected failure (docs/03_mcp_interface.md, 5);
``content_changed`` is journal-only (docs/01_requirements.md, 4.4): it marks
a cache entry whose body changed on re-fetch and never surfaces as a tool
error."""

type RenderMode = Literal["auto", "static", "browser"]
"""How the pipeline may obtain the page: static fetch, browser render, or auto."""

type PageFormat = Literal["markdown", "text"]
"""Rendering format of extracted page text."""

type DetailLevel = Literal["concise", "detailed"]
"""Verbosity of MCP response headers."""

type FetcherName = Literal["http", "browser"]
"""Which fetcher produced a page."""

type ToolName = Literal["fetch_page", "read_more", "find_in_page", "check_urls", "web_reload"]
"""Tool names; ``web_reload`` is the web UI's cache reload button and the
rest are the MCP tools (docs/03). Every recorded event names exactly one."""


class Clock(Protocol):
    """Source of the current UTC time, injected instead of datetime.now()."""

    def now(self) -> datetime:
        """Return the current aware UTC datetime."""
        ...  # pragma: no cover


def _require_non_blank(value: str, field: str) -> str:
    """Return ``value`` stripped of surrounding whitespace.

    Raises ValueError when nothing but whitespace remains. Used by value
    types whose whole point is "this string carries content".
    """
    stripped = value.strip()
    if not stripped:
        msg = f"{field} must not be empty or whitespace-only"
        raise ValueError(msg)
    return stripped


@dataclass(frozen=True, slots=True)
class NonEmptyStr:
    """A string guaranteed to carry non-blank content.

    Surrounding whitespace is stripped at construction (normalization, not
    validation): the stored value is the trimmed content.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_non_blank(self.value, "value"))


AGENT_ID_MAX_LENGTH = 64
"""Upper bound for agent identifiers supplied by MCP clients."""

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
"""Identifiers start with an alnum char and continue with alnum, dot, dash, underscore."""


def _validated_identifier(value: str, max_length: int, field: str) -> str:
    """Validate a human/agent-supplied identifier and return its stripped form."""
    stripped = _require_non_blank(value, field)
    if len(stripped) > max_length:
        msg = f"{field} must be at most {max_length} characters, got {len(stripped)}"
        raise ValueError(msg)
    if _IDENTIFIER_RE.fullmatch(stripped) is None:
        msg = (
            f"{field} may contain only ASCII letters, digits, dots, dashes and "
            f"underscores and must not start with a dot, dash or underscore, "
            f"got {stripped!r}"
        )
        raise ValueError(msg)
    return stripped


@dataclass(frozen=True, slots=True)
class AgentId(NonEmptyStr):
    """Identifier of an agent, taken from the ``agent`` parameter or header.

    Restricted charset and length keep the event log clean and make the
    web UI grouping safe.
    """

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validated_identifier(self.value, AGENT_ID_MAX_LENGTH, "agent id"),
        )


TASK_ID_MAX_LENGTH = 128
"""Upper bound for task identifiers supplied by MCP clients."""


@dataclass(frozen=True, slots=True)
class TaskId(NonEmptyStr):
    """Identifier of a task an agent groups its calls under (optional)."""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validated_identifier(self.value, TASK_ID_MAX_LENGTH, "task id"),
        )


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
"""Lowercase hex digest of SHA-256."""


@dataclass(frozen=True, slots=True)
class Sha256Hex:
    """A SHA-256 hex digest; uppercase input is normalized to lowercase."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.lower()
        if _SHA256_RE.fullmatch(normalized) is None:
            msg = f"sha256 hex digest must be 64 lowercase hex digits, got {self.value!r}"
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)


HTTP_STATUS_MIN = 100
"""Lowest HTTP status code reported by servers."""

HTTP_STATUS_MAX = 599
"""Highest HTTP status code reported by servers."""

HTTP_OK_MIN = 200
HTTP_OK_MAX = 299

HTTP_BLOCKED_STATUSES = (403, 429)
"""Status codes treated as blocked (S5, docs/01 section 4)."""

HTTP_NOT_FOUND_STATUSES = (404, 410)
"""Status codes treated as not found."""

HTTP_REDIRECT_CODES = (301, 302, 303, 307, 308)
"""Codes whose Location header a client follows."""

HTTP_TOO_MANY_REQUESTS = 429


@dataclass(frozen=True, slots=True)
class HttpStatus:
    """An HTTP status code (100-599)."""

    value: int

    def __post_init__(self) -> None:
        if not HTTP_STATUS_MIN <= self.value <= HTTP_STATUS_MAX:
            msg = f"HTTP status must be in {HTTP_STATUS_MIN}..{HTTP_STATUS_MAX}, got {self.value}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PositiveInt:
    """An integer strictly greater than zero."""

    value: int

    def __post_init__(self) -> None:
        if self.value <= 0:
            msg = f"value must be a positive integer, got {self.value}"
            raise ValueError(msg)
