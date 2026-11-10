"""TrafilaturaExtractor tests: extraction, metadata, formats, failures."""

import pytest

from web_extractor.domain.extract import Extracted, ExtractionFailure
from web_extractor.extractors.trafilatura_extractor import (
    TrafilaturaExtractor,
    _is_raw_html_dump,
)

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

_LINKED_ARTICLE = """
<html>
  <head><title>Test Headline</title></head>
  <body>
    <article>
      <h1>Big Heading</h1>
      <p>Read the <a href="https://example.test/source">full source report</a> for details.</p>
      <p>Paragraph two with another fact.</p>
    </article>
  </body>
</html>
"""


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


@pytest.mark.asyncio
async def test_extract_markdown_keeps_headings() -> None:
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_ARTICLE.encode(), "https://example.test/a", "markdown")
    assert isinstance(result, Extracted)
    assert "# Big Heading" in result.text
    assert result.format == "markdown"


@pytest.mark.asyncio
async def test_extract_markdown_keeps_links() -> None:
    """include_links=True: anchors inside the main text survive as markdown links."""
    extractor = TrafilaturaExtractor()
    result = await extractor.extract(_LINKED_ARTICLE.encode(), "https://example.test/a", "markdown")
    assert isinstance(result, Extracted)
    assert "[full source report](https://example.test/source)" in result.text


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


_SHORT_ARTICLE = (
    "<p>" + "The city council approved the new budget on Tuesday after a long debate. " * 7 + "</p>"
)


@pytest.mark.asyncio
async def test_short_article_with_noscript_comment_widget_is_not_needs_js() -> None:
    """Regression: a JS marker in <noscript> must not hide a real short article."""
    html = (
        "<html><head><title>Council approves budget</title></head><body><article>"
        f"<h1>Council approves budget</h1>{_SHORT_ARTICLE}</article>"
        "<noscript>Please enable JavaScript to view the comments.</noscript>"
        "</body></html>"
    ).encode()

    result = await TrafilaturaExtractor().extract(html, "https://news.test/a", "text")

    assert isinstance(result, Extracted)
    assert "budget" in result.text


@pytest.mark.asyncio
async def test_extracted_text_that_demands_javascript_is_needs_js() -> None:
    shell = (
        "JavaScript is not available. We have detected that JavaScript is disabled "
        "in this browser. Please enable JavaScript or switch to a supported browser "
        "to continue using this site. "
    ) * 3
    html = f"<html><body><article><p>{shell}</p></article></body></html>".encode()

    result = await TrafilaturaExtractor().extract(html, "https://x.test/post", "text")

    assert isinstance(result, ExtractionFailure)
    assert result.kind == "needs_js"


_ESCAPED_DUMP_PAGE = (
    b"<html><body><pre>&lt;!DOCTYPE html&gt;&lt;html&gt;&lt;body&gt;"
    b"challenge&lt;/body&gt;&lt;/html&gt;</pre></body></html>"
)

_DOUBLE_ESCAPED_DUMP_PAGE = (
    b"<html><head><title>ch</title></head><body><pre>"
    b"&amp;lt;html&amp;gt;&amp;lt;body&amp;gt;challenge"
    b"&amp;lt;/body&amp;gt;&amp;lt;/html&amp;gt;</pre></body></html>"
)


@pytest.mark.asyncio
async def test_escaped_source_markup_is_a_failed_extraction() -> None:
    """Regression: a challenge page's escaped HTML came through as ok text.

    trafilatura unescapes the visible text of the <pre> and hands back the
    page markup as the extracted "text"; that dump is a failed extraction,
    never content. The extractor is run three times because the case came
    from a nondeterministic production observation; the pinned trafilatura
    version returns the dump on every run.
    """
    for _ in range(3):
        result = await TrafilaturaExtractor().extract(
            _ESCAPED_DUMP_PAGE, "https://challenge.test/x", "text"
        )
        assert isinstance(result, ExtractionFailure)
        assert result.kind == "empty"
        assert "raw HTML markup" in result.message


@pytest.mark.asyncio
async def test_escaped_source_markup_in_markdown_is_a_failed_extraction() -> None:
    """The same dump through markdown output arrives backslash-escaped.

    trafilatura's markdown writer escapes the markup it hands through with
    backslashes ("\\<!DOCTYPE html>\\<html>..."), a third dump form that
    needs its own prefixes.
    """
    for _ in range(3):
        result = await TrafilaturaExtractor().extract(
            _ESCAPED_DUMP_PAGE, "https://challenge.test/x", "markdown"
        )
        assert isinstance(result, ExtractionFailure)
        assert result.kind == "empty"


@pytest.mark.asyncio
async def test_double_escaped_source_markup_is_a_failed_extraction() -> None:
    """A dump may also arrive from double-escaped markup (prefix ``<html``)."""
    for _ in range(3):
        result = await TrafilaturaExtractor().extract(
            _DOUBLE_ESCAPED_DUMP_PAGE, "https://challenge.test/x", "text"
        )
        assert isinstance(result, ExtractionFailure)
        assert result.kind == "empty"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<!DOCTYPE html><html><body>challenge</body></html>", True),
        ("<!doctype html><head><title>ch</title></head>", True),
        ('<html lang="en"><body>challenge</body></html>', True),
        ("&lt;!DOCTYPE html&gt;&lt;html&gt;&lt;body&gt;&lt;/body&gt;&lt;/html&gt;", True),
        ("&lt;html lang=&quot;en&quot;&gt;", True),
        ("\\<!DOCTYPE html>\\<html>\\<body>challenge\\</body>\\</html>", True),
        ('\\<html lang="en">', True),
        ("  \n\t<!DOCTYPE html><html><body>challenge</body></html>", True),
        ("  &lt;html&gt;&lt;body&gt;", True),
        # Only contains "<html", not at the start: real content.
        ('The article quotes a snippet: <html lang="en"> inside a code block.', False),
        ("# Report\n\nRead the [full source](https://example.test/one) for details.", False),
        # Only contains "\\<", not at the start: real markdown content.
        ("Prose with an escaped \\<angle\\> bracket in the middle of a sentence.", False),
        ("A plain article that starts with prose about everyday things.", False),
    ],
)
def test_is_raw_html_dump(*, text: str, expected: bool) -> None:
    """Table of dump prefixes: all three forms, case, whitespace, negatives.

    The entity-escaped prefixes cannot be produced through trafilatura (it
    unescapes before handing text over), so the escaped form is pinned here;
    the backslash-escaped form is what markdown output emits.
    """
    assert _is_raw_html_dump(text) is expected
