"""Text extraction from PDF documents with pypdf.

Court rulings and registry extracts often come as PDF. Parsing is CPU-bound
and runs in a worker thread. A PDF without a text layer (scans) has nothing
to extract: that is an ``empty`` failure with an explicit hint, not empty text
reported as success. OCR is out of scope.
"""

import asyncio
import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from web_extractor.domain.extract import (
    Extracted,
    ExtractionFailure,
    ExtractResult,
    PageMetadata,
)
from web_extractor.domain.types import PageFormat

MAX_PAGES = 400
"""Pages beyond this are skipped (and the text says so): bounds CPU and memory."""


class PdfExtractor:
    """Extracts the text layer of a PDF, with ``[page N]`` markers for citing."""

    async def extract(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        """Extract ``html`` (the PDF bytes); failures are values."""
        return await asyncio.to_thread(self._extract_sync, html, final_url, page_format)

    def _extract_sync(self, data: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                return ExtractionFailure("parse_error", f"PDF {final_url} is encrypted")
            total = len(reader.pages)
            parts: list[str] = []
            for number, page in enumerate(reader.pages[:MAX_PAGES], start=1):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    parts.append(f"[page {number}]\n{page_text}")
            info = reader.metadata
        except (PyPdfError, ValueError, OSError, RecursionError) as exc:
            return ExtractionFailure("parse_error", f"PDF {final_url} could not be parsed: {exc}")
        if not parts:
            msg = f"PDF {final_url} has no text layer (scanned images?); OCR is not supported"
            return ExtractionFailure("empty", msg)
        if total > MAX_PAGES:
            parts.append(f"[PDF truncated: first {MAX_PAGES} of {total} pages extracted]")
        metadata = PageMetadata(
            title=_clean(info.title) if info is not None else None,
            author=_clean(info.author) if info is not None else None,
        )
        return Extracted(text="\n\n".join(parts), format=page_format, metadata=metadata)


def _clean(value: str | None) -> str | None:
    """A metadata string, or None when absent or blank."""
    if value is None or not value.strip():
        return None
    return value.strip()
