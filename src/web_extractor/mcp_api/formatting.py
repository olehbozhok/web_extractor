"""Formatting of MCP tool responses (docs/03, sections 4 and 5).

The text block is a short ``key: value`` header followed by the page body
fenced between untrusted-content markers; ``structuredContent`` carries the
same metadata without the body, so clients that forward both never pay for
the body twice. Errors use ``ERROR <kind>: <what>. <what to do>``; when
switching fetchers may help, ``<what to do>`` starts with the render mode to
retry with, chosen from the fetcher that failed (docs/03, section 5).
"""

from collections.abc import Sequence

from web_extractor.domain.listing_links import LinkItem
from web_extractor.domain.types import ErrorKind, FetcherName, RenderMode

_BEGIN_MARKER = "--- BEGIN UNTRUSTED PAGE CONTENT ---"
_END_MARKER = "--- END UNTRUSTED PAGE CONTENT ---"

_BEGIN_LINKS_MARKER = "--- BEGIN UNTRUSTED LISTING LINKS ---"
_END_LINKS_MARKER = "--- END UNTRUSTED LISTING LINKS ---"

_ADVICE: dict[ErrorKind, str] = {
    "invalid_argument": "Fix the named parameter and call again.",
    "ssrf_blocked": "The address is private or forbidden by design; do not retry it.",
    "blocked": (
        "The site refused the request; if a retry does not help, open the URL "
        "with another tool. For 429, do not retry before Retry-After."
    ),
    "not_found": "Check the URL, look for an archived copy or another source.",
    "timeout": "Retry once.",
    "needs_browser": "The page has no extractable text without JavaScript.",
    "empty_render": "The page rendered but has no extractable text; use another source.",
    "too_large": "The page is too large for text extraction; use another source.",
    "dns": "The host name does not resolve; check the spelling of the domain.",
    "network": "Connection failed; retry once, then try another source.",
    "unsupported_type": (
        "The content type (image, archive, media) cannot be turned into text; use another source."
    ),
    "browser_unavailable": "The browser service is down; retry later.",
    "unknown_cursor": "Repeat fetch_page with the same URL.",
    "stale_cursor": "The page changed; repeat fetch_page with the same URL.",
    "internal": "Report the error; the message intentionally hides details.",
    "server_error": "Retry later or look for another source.",
    "redirect_error": "The site loops redirects; give up on this URL.",
    "content_changed": "Journal-only event; never shown to agents.",
}
"""Advice per error kind, independent of the fetcher (docs/03, section 5)."""

_RETRY_STATIC_AFTER_BROWSER: frozenset[ErrorKind] = frozenset(
    {"blocked", "timeout", "network", "server_error", "browser_unavailable"}
)
"""Browser failures a plain HTTP fetch may not share: anti-bot rules that
target Chromium, slow scripts, downloads (PDF) Chromium does not open."""

_RETRY_BROWSER_AFTER_STATIC: frozenset[ErrorKind] = frozenset({"blocked", "needs_browser"})
"""Static failures a real browser may get past: plain-client blocks, JS-only pages."""

_RETRY_HINT: dict[RenderMode, str] = {
    "static": (
        "Retry once with render=static (plain HTTP; the only way to get PDFs and "
        "other downloads, which the browser does not open)."
    ),
    "browser": "Retry once with render=browser if available.",
}


def retry_render(kind: ErrorKind, fetcher: FetcherName | None) -> RenderMode | None:
    """The render mode worth one retry after a failure, or ``None``.

    ``fetcher`` is the fetcher whose attempt failed (``None`` when no fetch
    happened, e.g. rejected arguments). The advice always switches to the
    other fetcher; failures that do not depend on the fetcher (404, DNS,
    forbidden address) get none.
    """
    match fetcher:
        case "browser":
            return "static" if kind in _RETRY_STATIC_AFTER_BROWSER else None
        case "http":
            return "browser" if kind in _RETRY_BROWSER_AFTER_STATIC else None
        case None:
            return None


def error_text(kind: ErrorKind, message: str, fetcher: FetcherName | None) -> str:
    """The single-line error text sent as the tool's content.

    ``fetcher`` is the fetcher that failed; it decides the retry advice.
    """
    advice = _ADVICE.get(kind, _ADVICE["internal"])
    mode = retry_render(kind, fetcher)
    if mode is not None:
        advice = f"{_RETRY_HINT[mode]} {advice}"
    return f"ERROR {kind}: {message}. {advice}"


def fenced_body(body: str) -> str:
    """Wrap page content between the untrusted-content markers."""
    return f"{_BEGIN_MARKER}\n{body}\n{_END_MARKER}"


def fenced_listing_links(links: Sequence[LinkItem]) -> str:
    """Wrap the listing links in their own untrusted-content fence.

    One link per line as ``- name — link``: the fence is presented as
    literal text (no markdown rendering, like the body), so link syntax
    would add nothing while forcing bracket escaping of arbitrary anchor
    names. The URL is the text after the last `` — `` of a line. Empty
    input never reaches here: no links, no section (docs/03).
    """
    lines = [f"- {item.name} — {item.link}" for item in links]
    return f"{_BEGIN_LINKS_MARKER}\n" + "\n".join(lines) + f"\n{_END_LINKS_MARKER}"


def chunk_line(start: int, end: int, total: int) -> str:
    """The ``chars:`` header line, with an explicit TRUNCATED/END OF TEXT flag."""
    flag = "TRUNCATED" if end < total else "END OF TEXT"
    return f"chars: {start}-{end} of {total} | {flag}"


def cursor_line(cursor_id: str) -> str:
    """The ``cursor:`` header line with the continuation hint."""
    return f"cursor: {cursor_id}   # call read_more with this cursor for the next chunk"
