"""Content verdict, date provenance, error kinds and check_urls details (docs/03, 3.5)."""

import pytest
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from mcp import ClientSession
from tests.mcp.conftest import McpHarness
from tests.service.service_helpers import extracted, fetched_ok, normalized
from web_extractor.domain.extract import Extracted, PageMetadata
from web_extractor.domain.fetch import FetchFailure

_URL = "https://site.test/article"
_LONG = "A sentence of the real article body. " * 40


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
async def test_fetch_page_reports_content_verdict(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("Instagram. Log in to continue.", page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "content: login_wall" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["content_quality"] == "login_wall"


@pytest.mark.asyncio
async def test_real_article_is_content_ok(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "content: ok" in _text_of(result)


@pytest.mark.asyncio
async def test_published_date_comes_with_its_source(mcp: McpHarness) -> None:
    metadata = PageMetadata(title="T", published_at="2026-03-01", published_source="json_ld")
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(Extracted(text=_LONG, format="markdown", metadata=metadata))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "published: 2026-03-01 (json_ld)" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["published_source"] == "json_ld"


@pytest.mark.asyncio
async def test_find_in_page_structured_matches_carry_context(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("before the needle after", page_format="text"))

    result = await _call(mcp, "find_in_page", {"url": _URL, "query": "needle", "context_chars": 7})

    assert result.structured_content is not None
    match = result.structured_content["matches"][0]
    assert (match["before"], match["match"], match["after"]) == ("re the ", "needle", " after")


@pytest.mark.asyncio
async def test_dns_failure_has_its_own_error_kind(mcp: McpHarness) -> None:
    mcp.fetcher.script(FetchFailure("dns", "no such host", final_url=normalized(_URL)))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert result.is_error is True
    assert "ERROR dns" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["error_kind"] == "dns"


@pytest.mark.asyncio
async def test_unsupported_type_has_its_own_error_kind(mcp: McpHarness) -> None:
    mcp.fetcher.script(
        FetchFailure("unsupported_type", "application/zip", final_url=normalized(_URL))
    )

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "ERROR unsupported_type" in _text_of(result)


@pytest.mark.asyncio
async def test_check_urls_deduplicates_and_reports_version_and_verdict(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("tiny", page_format="text"))

    result = await _call(mcp, "check_urls", {"urls": [_URL, _URL, f"{_URL}#frag"]})

    text = _text_of(result)
    assert len(mcp.fetcher.calls) == 1
    assert "2 duplicate" in text
    assert "content=thin" in text
    assert "text_version=" in text
    assert result.structured_content is not None
    rows = result.structured_content["url_rows"]
    assert len(rows) == 1
    assert rows[0]["content_quality"] == "thin"
    assert rows[0]["text_version"] is not None


@pytest.mark.asyncio
async def test_check_urls_and_fetch_page_agree_on_length_and_version(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))
    page = await _call(mcp, "fetch_page", {"url": _URL})

    check = await _call(mcp, "check_urls", {"urls": [_URL], "fresh": False})

    assert page.structured_content is not None
    assert check.structured_content is not None
    row = check.structured_content["url_rows"][0]
    assert row["text_version"] == page.structured_content["text_version"]
    assert row["text_chars"] == page.structured_content["chars_total"]
    assert row["text_format"] == page.structured_content["text_format"] == "markdown"


@pytest.mark.asyncio
async def test_version_headers_name_their_format(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))
    mcp.extractor.script(extracted(_LONG, page_format="text"))

    page = await _call(mcp, "fetch_page", {"url": _URL})
    found = await _call(mcp, "find_in_page", {"url": _URL, "query": "sentence"})

    assert "(markdown)" in _text_of(page).split("text_version:")[1].splitlines()[0]
    assert "(text)" in _text_of(found).split("text_version:")[1].splitlines()[0]
