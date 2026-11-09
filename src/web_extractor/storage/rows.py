"""Boundary parsing of raw SQLite values into typed fields.

Rows come back from aiosqlite with loosely typed columns; every accessor
here converts one column into a precise Python type and raises
:class:`DataCorruptionError` when the stored value does not fit. Database
content written by this service should never trip these checks; if it does,
that is an infrastructure failure, not an expected domain outcome.
"""

import sqlite3
from collections.abc import Mapping

from web_extractor.domain.types import ErrorKind, FetcherName, PageFormat, Status, ToolName


class DataCorruptionError(RuntimeError):
    """A stored value does not fit its declared type; the DB is corrupted."""


def row_value(row: sqlite3.Row, column: str) -> object:
    """One column of a row as ``object`` for further narrowing."""
    value: object = row[column]
    return value


def as_str(value: object, column: str) -> str:
    """A column that must hold TEXT."""
    if not isinstance(value, str):
        msg = f"column {column!r} must be TEXT, got {value!r}"
        raise DataCorruptionError(msg)
    return value


def as_opt_str(value: object, column: str) -> str | None:
    """A nullable TEXT column."""
    if value is None:
        return None
    return as_str(value, column)


def as_int(value: object, column: str) -> int:
    """A column that must hold INTEGER."""
    if not isinstance(value, int):
        msg = f"column {column!r} must be INTEGER, got {value!r}"
        raise DataCorruptionError(msg)
    return value


def as_opt_int(value: object, column: str) -> int | None:
    """A nullable INTEGER column."""
    if value is None:
        return None
    return as_int(value, column)


def as_bool(value: object, column: str) -> bool:
    """A NOT NULL INTEGER column storing a boolean (0 or 1)."""
    raw = as_int(value, column)
    if raw not in (0, 1):
        msg = f"column {column!r} must be 0 or 1, got {raw}"
        raise DataCorruptionError(msg)
    return raw == 1


def as_opt_bytes(value: object, column: str) -> bytes | None:
    """A nullable BLOB column."""
    if value is None:
        return None
    if not isinstance(value, bytes):
        msg = f"column {column!r} must be BLOB, got {type(value)!r}"
        raise DataCorruptionError(msg)
    return value


def _narrow[T](value: object, column: str, table: Mapping[str, T], what: str) -> T:
    """Look up a TEXT column in a table of allowed values, or fail."""
    text = as_str(value, column)
    narrowed = table.get(text)
    if narrowed is None:
        msg = f"column {column!r} must be {what}, got {text!r}"
        raise DataCorruptionError(msg)
    return narrowed


_STATUS_TABLE: dict[str, Status] = {
    "ok": "ok",
    "blocked": "blocked",
    "needs_browser": "needs_browser",
    "not_found": "not_found",
    "timeout": "timeout",
    "error": "error",
}
"""Status values as stored, mapping back onto the Literal type."""

_FETCHER_TABLE: dict[str, FetcherName] = {"http": "http", "browser": "browser"}

_PAGE_FORMAT_TABLE: dict[str, PageFormat] = {"markdown": "markdown", "text": "text"}

_TOOL_TABLE: dict[str, ToolName] = {
    "fetch_page": "fetch_page",
    "read_more": "read_more",
    "find_in_page": "find_in_page",
    "check_urls": "check_urls",
}

_ERROR_KIND_TABLE: dict[str, ErrorKind] = {
    "invalid_argument": "invalid_argument",
    "ssrf_blocked": "ssrf_blocked",
    "blocked": "blocked",
    "not_found": "not_found",
    "timeout": "timeout",
    "needs_browser": "needs_browser",
    "too_large": "too_large",
    "browser_unavailable": "browser_unavailable",
    "unknown_cursor": "unknown_cursor",
    "stale_cursor": "stale_cursor",
    "internal": "internal",
    "server_error": "server_error",
    "redirect_error": "redirect_error",
    "content_changed": "content_changed",
}


def as_status(value: object, column: str) -> Status:
    """A column holding one of the known Status strings."""
    return _narrow(value, column, _STATUS_TABLE, "a known status")


def as_opt_status(value: object, column: str) -> Status | None:
    """A nullable Status column."""
    if value is None:
        return None
    return _narrow(value, column, _STATUS_TABLE, "a known status")


def as_fetcher(value: object, column: str) -> FetcherName:
    """A column holding one of the known fetcher names."""
    return _narrow(value, column, _FETCHER_TABLE, "a known fetcher")


def as_opt_fetcher(value: object, column: str) -> FetcherName | None:
    """A nullable fetcher column."""
    if value is None:
        return None
    return _narrow(value, column, _FETCHER_TABLE, "a known fetcher")


def as_tool(value: object, column: str) -> ToolName:
    """A column holding one of the known tool names."""
    return _narrow(value, column, _TOOL_TABLE, "a known tool name")


def as_opt_error_kind(value: object, column: str) -> ErrorKind | None:
    """A nullable error-kind column."""
    if value is None:
        return None
    return _narrow(value, column, _ERROR_KIND_TABLE, "a known error kind")


def as_opt_page_format(value: object, column: str) -> PageFormat | None:
    """A nullable column holding one of the known page formats."""
    if value is None:
        return None
    return _narrow(value, column, _PAGE_FORMAT_TABLE, "a known page format")
