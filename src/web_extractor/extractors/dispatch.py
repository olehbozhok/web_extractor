"""Chooses the extractor by what the body actually is.

The body is sniffed (``%PDF-`` signature) rather than trusting the URL or the
Content-Type header: servers routinely serve PDFs as octet-stream or from URLs
without an extension.
"""

from typing import Final

from web_extractor.domain.extract import ExtractResult
from web_extractor.domain.types import PageFormat
from web_extractor.extractors.base import Extractor

_PDF_SIGNATURE: Final = b"%PDF-"
_SNIFF_BYTES: Final = 1024
"""The PDF spec tolerates junk before the signature within the first kilobyte."""


class ContentExtractor:
    """Routes PDF bodies to the PDF extractor and everything else to HTML."""

    def __init__(self, *, html: Extractor, pdf: Extractor) -> None:
        self._html = html
        self._pdf = pdf

    async def extract(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        """Extract with the extractor matching the body's signature."""
        target = self._pdf if _PDF_SIGNATURE in html[:_SNIFF_BYTES] else self._html
        return await target.extract(html, final_url, page_format)
