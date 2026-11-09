"""Extractor protocol: turns fetched HTML into text and metadata.

Implementations run CPU-heavy parsing off the event loop and must return
``ExtractResult`` — an empty page is an ``ExtractionFailure`` value, never
empty text with a success flag.
"""

from typing import Protocol

from web_extractor.domain.extract import ExtractResult
from web_extractor.domain.types import PageFormat


class Extractor(Protocol):
    """Extracts the main text and metadata from HTML."""

    async def extract(self, html: bytes, final_url: str, page_format: PageFormat) -> ExtractResult:
        """Extract ``html``; never raises for expected extraction failures."""
        ...  # pragma: no cover
