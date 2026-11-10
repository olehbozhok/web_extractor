"""listing_links in tool responses: first chunk only, count, sanitization (docs/03)."""

import pytest
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from mcp import ClientSession
from tests.mcp.conftest import McpHarness
from tests.service.service_helpers import extracted, fetched_ok, normalized

_URL = "https://news.test/index"
_LONG = "A sentence of the real article body. " * 40
_PAGE_BODY = (
    "<html><body>"
    '<a href="/a">First \U0001f996 headline</a>'
    '<a href="/b">Second headline</a>'
    '<a href="https://other.test/x">Foreign headline</a>'
    "</body></html>"
).encode()


def _text_of(result: CallToolResult) -> str:
    return "\n".join(content.text for content in result.content if isinstance(content, TextContent))


async def _call(mcp: McpHarness, tool: str, arguments: dict[str, object]) -> CallToolResult:
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await session.call_tool(tool, arguments)


@pytest.mark.asyncio
async def test_first_chunk_carries_listing_links_unchanged(mcp: McpHarness) -> None:
    """Same-site links only; anchor text reaches the response as it is on the
    page: astral characters are not replaced (docs/03, no-sanitization)."""
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), _PAGE_BODY))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL, "max_chars": 100})

    assert result.structured_content is not None
    assert result.structured_content["listing_links"] == [
        {"name": "First \U0001f996 headline", "link": "https://news.test/a"},
        {"name": "Second headline", "link": "https://news.test/b"},
    ]
    assert "listing_links: 2" in _text_of(result)


@pytest.mark.asyncio
async def test_first_chunk_text_carries_the_fenced_listing_section(mcp: McpHarness) -> None:
    """The array is duplicated into the text block: clients that forward only
    the text channel must still hand the links to the model (docs/03, 4)."""
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), _PAGE_BODY))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL, "max_chars": 100})

    text = _text_of(result)
    assert "--- BEGIN UNTRUSTED LISTING LINKS ---" in text
    assert "- First \U0001f996 headline — https://news.test/a" in text
    assert "- Second headline — https://news.test/b" in text
    assert "--- END UNTRUSTED LISTING LINKS ---" in text
    # The section stands outside the page body, after its fence.
    assert text.index("--- END UNTRUSTED PAGE CONTENT ---") < text.index(
        "--- BEGIN UNTRUSTED LISTING LINKS ---"
    )


@pytest.mark.asyncio
async def test_continuation_chunk_carries_no_listing_links(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), _PAGE_BODY))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))
    first = await _call(mcp, "fetch_page", {"url": _URL, "max_chars": 100})
    assert first.structured_content is not None
    cursor = first.structured_content["cursor"]
    assert cursor is not None

    more = await _call(mcp, "read_more", {"cursor": cursor})

    assert more.structured_content is not None
    assert more.structured_content["listing_links"] is None
    assert "listing_links" not in _text_of(more)
    assert "UNTRUSTED LISTING LINKS" not in _text_of(more)


@pytest.mark.asyncio
async def test_find_in_page_carries_no_listing_section(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), _PAGE_BODY))
    mcp.extractor.script(extracted(_LONG))

    result = await _call(mcp, "find_in_page", {"url": _URL, "query": "sentence"})

    assert "UNTRUSTED LISTING LINKS" not in _text_of(result)


@pytest.mark.asyncio
async def test_pdf_answer_has_no_listing_links(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), b"%PDF-1.4 fake body"))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert result.structured_content is not None
    assert result.structured_content["document"] == "pdf"
    assert result.structured_content["listing_links"] is None
    assert "listing_links" not in _text_of(result)


@pytest.mark.asyncio
async def test_html_without_anchors_reports_an_empty_array(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(
        fetched_ok(normalized(_URL), b"<html><body><p>No anchors at all.</p></body></html>")
    )
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert result.structured_content is not None
    assert result.structured_content["listing_links"] == []
    # An empty array builds no section; the header count line stays.
    assert "listing_links: 0" in _text_of(result)
    assert "UNTRUSTED LISTING LINKS" not in _text_of(result)


@pytest.mark.asyncio
async def test_check_urls_rows_carry_the_listing_links_count(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL), _PAGE_BODY))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "check_urls", {"urls": [_URL]})

    assert result.structured_content is not None
    row = result.structured_content["url_rows"][0]
    assert row["listing_links_count"] == 2
    # The array itself stays out of check_urls: it would inflate batch answers.
    assert "listing_links" not in row
