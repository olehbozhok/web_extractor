"""PdfExtractor and the content-sniffing dispatcher."""

import pytest

from tests.extractors.pdf_builder import build_pdf
from web_extractor.domain.extract import Extracted, ExtractionFailure
from web_extractor.extractors.dispatch import ContentExtractor
from web_extractor.extractors.pdf_extractor import PdfExtractor
from web_extractor.extractors.trafilatura_extractor import TrafilaturaExtractor

_URL = "https://court.test/ruling.pdf"


@pytest.mark.asyncio
async def test_pdf_text_and_metadata_are_extracted() -> None:
    pdf = build_pdf(
        ["Ruling number 42", "Second page text"], title="Court ruling", author="Registry"
    )

    result = await PdfExtractor().extract(pdf, _URL, "text")

    assert isinstance(result, Extracted)
    assert "Ruling number 42" in result.text
    assert "Second page text" in result.text
    assert "[page 2]" in result.text
    assert result.metadata.title == "Court ruling"
    assert result.metadata.author == "Registry"
    assert result.metadata.published_at is None


@pytest.mark.asyncio
async def test_pdf_without_text_is_empty_failure() -> None:
    result = await PdfExtractor().extract(build_pdf([""]), _URL, "text")

    assert isinstance(result, ExtractionFailure)
    assert result.kind == "empty"
    assert "scanned" in result.message


@pytest.mark.asyncio
async def test_corrupt_pdf_is_parse_error_value() -> None:
    result = await PdfExtractor().extract(b"%PDF-1.4\nthis is not a pdf", _URL, "text")

    assert isinstance(result, ExtractionFailure)
    assert result.kind == "parse_error"


@pytest.mark.asyncio
async def test_dispatcher_routes_pdf_by_signature_not_by_url() -> None:
    dispatcher = ContentExtractor(html=TrafilaturaExtractor(), pdf=PdfExtractor())
    html_page = (
        b"<html><body><article><p>"
        + b"Plain html sentence. " * 30
        + b"</p></article></body></html>"
    )

    pdf_result = await dispatcher.extract(
        build_pdf(["Registry entry"]), "https://x.test/doc", "text"
    )
    html_result = await dispatcher.extract(html_page, "https://x.test/page.pdf", "text")

    assert isinstance(pdf_result, Extracted)
    assert "Registry entry" in pdf_result.text
    assert isinstance(html_result, Extracted)
    assert "Plain html sentence." in html_result.text
