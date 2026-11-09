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

_JS_REQUIRED_MARKERS = (
    "enable javascript",
    "enable js",
    "javascript is required",
    "requires javascript",
    "please turn on javascript",
    "enable-javascript",
)
"""Lowercase markers of JS-only pages; detection is heuristic by design."""

_JS_STUB_TEXT_LIMIT = 1000
"""Short extractions over a page that also carries JS-required markers are
treated as JS-only stubs (trafilatura returns the noscript boilerplate)."""

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
        if len(text) <= _JS_STUB_TEXT_LIMIT and _looks_like_js_page(html):
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
        return PageMetadata(
            title=document.title,
            author=document.author,
            language=document.language,
            publisher=document.sitename,
        )


def _looks_like_js_page(html: bytes) -> bool:
    """True when the HTML carries explicit JS-required markers.

    Case-insensitive scan of the raw bytes decoded leniently; a heuristic
    that errs toward ``empty`` when unsure.
    """
    lowered = html.decode("utf-8", errors="replace").lower()
    return any(marker in lowered for marker in _JS_REQUIRED_MARKERS)
