"""Chooses the extractor by what the body actually is.

The body is sniffed (``%PDF-`` signature) rather than trusting the URL or the
Content-Type header: servers routinely serve PDFs as octet-stream or from URLs
without an extension.
"""

from web_extractor.domain.content_type import sniff_document
from web_extractor.domain.extract import ExtractResult
from web_extractor.domain.types import PageFormat
from web_extractor.extractors.base import Extractor


class ContentExtractor:
    """Routes PDF bodies to the PDF extractor and everything else to HTML."""

    def __init__(self, *, html: Extractor, pdf: Extractor) -> None:
        self._html = html
        self._pdf = pdf

    async def extract(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        """Extract with the extractor matching the body's signature."""
        target = self._pdf if sniff_document(html) == "pdf" else self._html
        return await target.extract(html, final_url, page_format)
