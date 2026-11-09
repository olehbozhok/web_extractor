"""TrafilaturaExtractor tests: extraction, metadata, formats, failures."""

import pytest

from web_extractor.domain.extract import Extracted, ExtractionFailure
from web_extractor.extractors.trafilatura_extractor import TrafilaturaExtractor

_ARTICLE = """
<html>
  <head>
    <title>Test Headline</title>
    <meta name="author" content="Jane Doe">
    <meta name="date" content="2026-01-05">
    <meta name="sitename" content="Example News">
  </head>
  <body>
    <article>
      <h1>Big Heading</h1>
      <p>Paragraph one with a fact.</p>
      <p>Paragraph two with another fact.</p>
    </article>
  </body>
</html>
"""

_JS_PAGE = b"""
<html><body>
<noscript>Please enable JavaScript to view this page.</noscript>
<div id="root"></div>
<script>document.write('app');</script>
</body></html>
"""

_SPARSE_PAGE = b"<html><body><div></div></body></html>"


@pytest.mark.asyncio
async def test_extract_text_with_metadata() -> None:
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_ARTICLE.encode(), "https://example.test/a", "text")
    assert isinstance(result, Extracted)
    assert "Big Heading" in result.text
    assert "Paragraph one" in result.text
    assert result.format == "text"
    # trafilatura may prefer the h1 over the <title>; both carry the heading.
    assert result.metadata.title is not None and "Heading" in result.metadata.title
    assert result.metadata.author == "Jane Doe"
    assert result.metadata.published_at == "2026-01-05"


@pytest.mark.asyncio
async def test_extract_markdown_keeps_headings() -> None:
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_ARTICLE.encode(), "https://example.test/a", "markdown")
    assert isinstance(result, Extracted)
    assert "# Big Heading" in result.text
    assert result.format == "markdown"


@pytest.mark.asyncio
async def test_js_page_reports_needs_js() -> None:
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_JS_PAGE, "https://example.test/app", "text")
    assert isinstance(result, ExtractionFailure)
    assert result.kind == "needs_js"


@pytest.mark.asyncio
async def test_sparse_page_reports_empty() -> None:
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_SPARSE_PAGE, "https://example.test/s", "text")
    assert isinstance(result, ExtractionFailure)
    assert result.kind == "empty"


@pytest.mark.asyncio
async def test_undecodable_bytes_report_parse_error() -> None:
    extractor = TrafilaturaExtractor()
    # A UTF-16 byte order mark with no valid UTF-8 content to fall back on.
    result = await extractor.extract(
        b"\xff\xfe<\x00h\x00t\x00m\x00l\x00>\x00", "https://example.test/x", "text"
    )
    assert isinstance(result, ExtractionFailure)
    assert result.kind in ("parse_error", "empty")
