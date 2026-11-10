"""Content verdict, date provenance, error kinds and check_urls details (docs/03, 3.5)."""

from dataclasses import replace

import pytest
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from mcp import ClientSession
from tests.mcp.conftest import McpHarness
from tests.service.service_helpers import extracted, fetched_blocked, fetched_ok, normalized
from web_extractor.domain.extract import Extracted, PageMetadata
from web_extractor.domain.fetch import FetchFailure
from web_extractor.domain.types import HttpStatus

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
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("Instagram. Log in to continue.", page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "content: login_wall" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["content_quality"] == "login_wall"


@pytest.mark.asyncio
async def test_real_article_is_content_ok(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "content: ok" in _text_of(result)


@pytest.mark.asyncio
async def test_find_in_page_structured_matches_carry_context(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("before the needle after", page_format="text"))

    result = await _call(mcp, "find_in_page", {"url": _URL, "query": "needle", "context_chars": 7})

    assert result.structured_content is not None
    match = result.structured_content["matches"][0]
    assert (match["before"], match["match"], match["after"]) == ("re the ", "needle", " after")


@pytest.mark.asyncio
async def test_dns_failure_has_its_own_error_kind(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(FetchFailure("dns", "no such host", final_url=normalized(_URL)))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert result.is_error is True
    assert "ERROR dns" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["error_kind"] == "dns"


@pytest.mark.asyncio
async def test_unsupported_type_has_its_own_error_kind(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(
        FetchFailure("unsupported_type", "application/zip", final_url=normalized(_URL))
    )

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert "ERROR unsupported_type" in _text_of(result)


@pytest.mark.asyncio
async def test_check_urls_deduplicates_and_reports_version_and_verdict(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("tiny", page_format="text"))

    result = await _call(mcp, "check_urls", {"urls": [_URL, _URL, f"{_URL}#frag"]})

    text = _text_of(result)
    assert len(mcp.browser_fetcher.calls) == 1
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
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
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
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))
    mcp.extractor.script(extracted(_LONG, page_format="text"))

    page = await _call(mcp, "fetch_page", {"url": _URL})
    found = await _call(mcp, "find_in_page", {"url": _URL, "query": "sentence"})

    assert "(markdown)" in _text_of(page).split("text_version:")[1].splitlines()[0]
    assert "(text)" in _text_of(found).split("text_version:")[1].splitlines()[0]


@pytest.mark.asyncio
async def test_find_in_page_is_case_insensitive_by_default_and_reports_total(
    mcp: McpHarness,
) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("ПФЖ; ПФЖ; пфж.", page_format="text"))

    result = await _call(mcp, "find_in_page", {"url": _URL, "query": "пфж", "max_matches": 2})

    assert result.structured_content is not None
    assert result.structured_content["total_matches"] == 3
    assert len(result.structured_content["matches"]) == 2
    assert result.structured_content["matches"][0]["verbatim"] is False  # case differed
    assert "matches: 2 of 3 (case-insensitive)" in _text_of(result)


@pytest.mark.asyncio
async def test_case_sensitive_search_is_still_available(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("ПФЖ; ПФЖ; пфж.", page_format="text"))

    result = await _call(mcp, "find_in_page", {"url": _URL, "query": "пфж", "ignore_case": False})

    assert result.structured_content is not None
    assert result.structured_content["total_matches"] == 1


@pytest.mark.asyncio
async def test_read_more_keeps_page_metadata(mcp: McpHarness) -> None:
    metadata = PageMetadata(title="Long story", author="A. Writer")
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(Extracted(text=_LONG, format="markdown", metadata=metadata))
    first = await _call(mcp, "fetch_page", {"url": _URL, "max_chars": 100})
    assert first.structured_content is not None

    more = await _call(mcp, "read_more", {"cursor": first.structured_content["cursor"]})

    assert more.structured_content is not None
    assert more.structured_content["title"] == "Long story"
    assert more.structured_content["author"] == "A. Writer"
    assert more.structured_content["content_quality"] == "ok"


@pytest.mark.asyncio
async def test_find_in_page_can_search_markdown_and_names_the_mode(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("A **bold** claim about the budget.", page_format="markdown"))

    result = await _call(
        mcp, "find_in_page", {"url": _URL, "query": "**bold** claim", "format": "markdown"}
    )

    assert result.structured_content is not None
    assert result.structured_content["text_format"] == "markdown"
    assert result.structured_content["ignore_case"] is True
    assert result.structured_content["total_matches"] == 1
    text = _text_of(result)
    assert "(markdown)" in text.split("text_version:")[1].splitlines()[0]
    assert "(case-insensitive)" in text


@pytest.mark.asyncio
async def test_document_kind_is_sniffed_and_declared_type_shown_when_detailed(
    mcp: McpHarness,
) -> None:
    """A PDF served as text/html: document says pdf, content_type shows the label."""
    fetched = fetched_ok(normalized(_URL), b"%PDF-1.4 fake body")
    mislabeled = replace(fetched, headers={"Content-Type": "text/html; charset=utf-8"})
    mcp.browser_fetcher.script(mislabeled)
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    concise = await _call(mcp, "fetch_page", {"url": _URL})
    detailed = await _call(mcp, "fetch_page", {"url": _URL, "detail": "detailed"})

    assert "document: pdf" in _text_of(concise)
    assert concise.structured_content is not None
    assert concise.structured_content["document"] == "pdf"
    assert concise.structured_content["content_type"] is None  # only when detailed
    assert "content_type: text/html" in _text_of(detailed)
    assert detailed.structured_content is not None
    assert detailed.structured_content["content_type"] == "text/html"


@pytest.mark.asyncio
async def test_check_urls_rows_carry_the_document_kind(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "check_urls", {"urls": [_URL]})

    assert "document=html" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["url_rows"][0]["document"] == "html"


@pytest.mark.asyncio
async def test_structured_publication_date_is_shown_with_its_source(mcp: McpHarness) -> None:
    metadata = PageMetadata(title="T", published_at="2026-03-01", published_source="json_ld")
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(Extracted(text=_LONG, format="markdown", metadata=metadata))

    page = await _call(mcp, "fetch_page", {"url": _URL, "max_chars": 100})
    assert page.structured_content is not None
    more = await _call(mcp, "read_more", {"cursor": page.structured_content["cursor"]})

    assert "published: 2026-03-01 (json_ld)" in _text_of(page)
    assert page.structured_content["published_source"] == "json_ld"
    assert more.structured_content is not None
    assert more.structured_content["published_at"] == "2026-03-01"


@pytest.mark.asyncio
async def test_no_date_line_without_a_structured_date(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    page = await _call(mcp, "fetch_page", {"url": _URL})

    assert "published:" not in _text_of(page)
    assert page.structured_content is not None
    assert page.structured_content["published_at"] is None


@pytest.mark.asyncio
async def test_every_page_reporting_tool_gives_the_date_the_same_way(mcp: McpHarness) -> None:
    metadata = PageMetadata(title="T", published_at="2026-03-01", published_source="meta")
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(Extracted(text=_LONG, format="markdown", metadata=metadata))
    mcp.extractor.script(Extracted(text=_LONG, format="text", metadata=metadata))

    checked = await _call(mcp, "check_urls", {"urls": [_URL]})
    found = await _call(mcp, "find_in_page", {"url": _URL, "query": "sentence"})

    assert "published=2026-03-01 (meta)" in _text_of(checked)
    assert checked.structured_content is not None
    row = checked.structured_content["url_rows"][0]
    assert (row["published_at"], row["published_source"]) == ("2026-03-01", "meta")
    assert "published: 2026-03-01 (meta)" in _text_of(found)
    assert found.structured_content is not None
    assert found.structured_content["published_source"] == "meta"


@pytest.mark.asyncio
async def test_check_urls_root_has_no_page_fields(mcp: McpHarness) -> None:
    """Page fields live in url_rows; the root never shows a null in their place."""
    mcp.browser_fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG, page_format="markdown"))

    result = await _call(mcp, "check_urls", {"urls": [_URL, _URL]})

    assert result.structured_content is not None
    assert set(result.structured_content) == {
        "status",
        "urls_checked",
        "duplicates_skipped",
        "summary",
        "url_rows",
    }
    assert result.structured_content["urls_checked"] == 1
    assert result.structured_content["duplicates_skipped"] == 1


@pytest.mark.asyncio
async def test_error_root_carries_only_error_fields(mcp: McpHarness) -> None:
    mcp.browser_fetcher.script(FetchFailure("dns", "no such host", final_url=normalized(_URL)))

    result = await _call(mcp, "fetch_page", {"url": _URL})

    assert result.structured_content is not None
    assert set(result.structured_content) == {
        "status",
        "url",
        "error_kind",
        "error_message",
        "fetcher",
        "retry_render",
    }
    assert result.structured_content["status"] == "error"


@pytest.mark.asyncio
async def test_static_block_error_advises_browser_retry(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_blocked(normalized(_URL)))

    result = await _call(mcp, "fetch_page", {"url": _URL, "render": "static"})

    assert result.is_error is True
    assert "render=browser" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["fetcher"] == "http"
    assert result.structured_content["retry_render"] == "browser"


@pytest.mark.asyncio
async def test_check_urls_row_carries_retry_advice(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_blocked(normalized(_URL)))

    result = await _call(mcp, "check_urls", {"urls": [_URL], "render": "static"})

    assert "| retry: render=browser" in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["url_rows"][0]["retry_render"] == "browser"


@pytest.mark.asyncio
async def test_not_found_gives_no_render_advice(mcp: McpHarness) -> None:
    mcp.fetcher.script(
        FetchFailure("not_found", "404", http_status=HttpStatus(404), final_url=normalized(_URL))
    )

    result = await _call(mcp, "fetch_page", {"url": _URL, "render": "static"})

    assert "render=" not in _text_of(result)
    assert result.structured_content is not None
    assert result.structured_content["retry_render"] is None
