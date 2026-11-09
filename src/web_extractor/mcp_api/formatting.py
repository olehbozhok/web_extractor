"""Formatting of MCP tool responses (docs/03, sections 4 and 5).

The text block is a short ``key: value`` header followed by the page body
fenced between untrusted-content markers; ``structuredContent`` carries the
same metadata without the body, so clients that forward both never pay for
the body twice. Errors use ``ERROR <kind>: <what>. <what to do>``.
"""

from web_extractor.domain.types import ErrorKind

_BEGIN_MARKER = "--- BEGIN UNTRUSTED PAGE CONTENT ---"
_END_MARKER = "--- END UNTRUSTED PAGE CONTENT ---"

_ADVICE: dict[ErrorKind, str] = {
    "invalid_argument": "Fix the named parameter and call again.",
    "ssrf_blocked": "The address is private or forbidden by design; do not retry it.",
    "blocked": (
        "Try render=browser if available; if that fails, open the URL with "
        "another tool. For 429, do not retry before Retry-After."
    ),
    "not_found": "Check the URL, look for an archived copy or another source.",
    "timeout": "Retry once; if the page was browser-rendered, try render=static.",
    "needs_browser": "Repeat with render=browser if available.",
    "too_large": "The page is too large for text extraction; use another source.",
    "browser_unavailable": "Retry later, or use render=static.",
    "unknown_cursor": "Repeat fetch_page with the same URL.",
    "stale_cursor": "The page changed; repeat fetch_page with the same URL.",
    "internal": "Report the error; the message intentionally hides details.",
    "server_error": "Retry later or look for another source.",
    "redirect_error": "The site loops redirects; give up on this URL.",
    "content_changed": "Journal-only event; never shown to agents.",
}
"""Advice per error kind (docs/03, section 5)."""


def error_text(kind: ErrorKind, message: str) -> str:
    """The single-line error text sent as the tool's content."""
    advice = _ADVICE.get(kind, _ADVICE["internal"])
    return f"ERROR {kind}: {message}. {advice}"


def fenced_body(body: str) -> str:
    """Wrap page content between the untrusted-content markers."""
    return f"{_BEGIN_MARKER}\n{body}\n{_END_MARKER}"


def chunk_line(start: int, end: int, total: int) -> str:
    """The ``chars:`` header line, with an explicit TRUNCATED/END OF TEXT flag."""
    flag = "TRUNCATED" if end < total else "END OF TEXT"
    return f"chars: {start}-{end} of {total} | {flag}"


def cursor_line(cursor_id: str) -> str:
    """The ``cursor:`` header line with the continuation hint."""
    return f"cursor: {cursor_id}   # call read_more with this cursor for the next chunk"
