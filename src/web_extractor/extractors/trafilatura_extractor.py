"""Main-text extraction with trafilatura.

trafilatura runs in a worker thread (``asyncio.to_thread``): parsing a large
page is CPU-bound and must not block the event loop. The extractor knows
nothing about which fetcher produced the HTML.
"""

import asyncio

import trafilatura

from web_extractor.domain.extract import (
    Extracted,
    ExtractionFailure,
    ExtractResult,
    PageMetadata,
)
from web_extractor.domain.types import PageFormat
from web_extractor.extractors.published_date import find_published_date

_JS_REQUIRED_MARKERS = (
    "enable javascript",
    "enable js",
    "javascript is required",
    "requires javascript",
    "please turn on javascript",
    "enable-javascript",
    "javascript is not available",
    "javascript is disabled",
)
"""Lowercase markers of JS-only pages; detection is heuristic by design."""

_JS_STUB_TEXT_LIMIT = 200
"""Extractions this short over a page whose *raw HTML* carries JS-required
markers are JS-only stubs (trafilatura returned the noscript boilerplate). Kept
low on purpose: ordinary pages often carry such markers in ``<noscript>`` (e.g.
a comments widget), and a real short article must not become ``needs_js``."""

_JS_TEXT_MARKER_LIMIT = 1000
"""Extractions up to this length whose *extracted text itself* says JavaScript is
required are JS-only shells (e.g. "JavaScript is not available" pages)."""

_OUTPUT_FORMATS: dict[PageFormat, str] = {
    "markdown": "markdown",
    "text": "txt",
}
"""Requested format names in trafilatura's vocabulary."""


class TrafilaturaExtractor:
    """Extracts main text and metadata using trafilatura in a thread."""

    async def extract(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        """Extract ``html``; failures (empty, JS-only, undecodable) are values."""
        return await asyncio.to_thread(self._extract_sync, html, final_url, page_format)

    def _extract_sync(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        output_format = _OUTPUT_FORMATS[page_format]
        try:
            text = trafilatura.extract(
                html,
                url=final_url,
                output_format=output_format,
                include_comments=False,
                include_tables=True,
                # Links are part of the page an agent reads: without them the
                # markdown hides navigation and headline lists entirely. For
                # txt output trafilatura ignores the flag, which is fine.
                include_links=True,
            )
        except UnicodeDecodeError:
            msg = f"page {final_url} is not decodable as text"
            return ExtractionFailure("parse_error", msg)
        if text is None or not text.strip():
            if _looks_like_js_page(html):
                msg = f"page {final_url} renders only with JavaScript"
                return ExtractionFailure("needs_js", msg)
            msg = f"no main text found on page {final_url}"
            return ExtractionFailure("empty", msg)
        if (len(text) <= _JS_STUB_TEXT_LIMIT and _looks_like_js_page(html)) or (
            len(text) <= _JS_TEXT_MARKER_LIMIT and _has_js_marker(text)
        ):
            msg = f"page {final_url} renders only with JavaScript (stub text extracted)"
            return ExtractionFailure("needs_js", msg)
        return Extracted(text=text, format=page_format, metadata=self._metadata(html, final_url))

    def _metadata(self, html: bytes, final_url: str) -> PageMetadata:
        """Best-effort metadata; absence of any field stays None."""
        try:
            document = trafilatura.extract_metadata(html, default_url=final_url, extensive=False)
        except UnicodeDecodeError:
            document = None
        if document is None:
            return PageMetadata()
        published = find_published_date(html.decode("utf-8", errors="replace"))
        return PageMetadata(
            title=document.title,
            author=document.author,
            published_at=published.value if published is not None else None,
            published_source=published.source if published is not None else None,
            language=document.language,
            publisher=document.sitename,
        )


def _looks_like_js_page(html: bytes) -> bool:
    """True when the HTML carries explicit JS-required markers.

    Case-insensitive scan of the raw bytes decoded leniently; a heuristic
    that errs toward ``empty`` when unsure.
    """
    return _has_js_marker(html.decode("utf-8", errors="replace"))


def _has_js_marker(text: str) -> bool:
    """True when ``text`` contains a JS-required marker (case-insensitive)."""
    lowered = text.lower()
    return any(marker in lowered for marker in _JS_REQUIRED_MARKERS)
