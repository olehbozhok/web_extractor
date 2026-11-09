"""End-to-end format behavior through the real pipeline and real extractor.

Unit tests use a scripted extractor that returns whatever it is told, so they
cannot prove a requested format is actually produced. These run the shipped
code path (Trafilatura included) over a fixture page that has markup.
"""

import tempfile
from pathlib import Path

import pytest
from evals.mcp_client import evaluation_server

_PAGE = "http://fixtures.test/quote"


@pytest.mark.asyncio
async def test_find_in_page_format_really_switches_the_searched_text() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            plain = await tools.call("find_in_page", {"url": _PAGE, "query": "Statement of Record"})
            markdown = await tools.call(
                "find_in_page",
                {"url": _PAGE, "query": "Statement of Record", "format": "markdown"},
            )
            page = await tools.call("fetch_page", {"url": _PAGE})
            checked = await tools.call("check_urls", {"urls": [_PAGE], "fresh": False})

    assert plain.meta["text_format"] == "text"
    assert markdown.meta["text_format"] == "markdown"
    assert plain.meta["text_version"] != markdown.meta["text_version"]
    assert plain.meta["chars_total"] != markdown.meta["chars_total"]
    # The markdown search sees the same text fetch_page returns, and says so.
    assert markdown.meta["text_version"] == page.meta["text_version"]
    assert markdown.meta["chars_total"] == page.meta["chars_total"]
    rows = checked.meta["url_rows"]
    assert isinstance(rows, list)
    assert rows[0]["text_version"] == page.meta["text_version"]
    assert rows[0]["text_format"] == "markdown"
