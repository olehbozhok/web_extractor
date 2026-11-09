"""MCP tool integration tests through a real SDK client (docs/03).

Covers: tools/list shape and annotations, fetch_page success and
truncation, cursor continuation to END OF TEXT, unknown and stale cursors,
find_in_page verbatim/normalized matches, check_urls order and summary,
argument-level rejections, agent id from the X-Agent-Id header, and
health endpoints.
"""

import pytest
from httpx import AsyncClient
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import CallToolResult, TextContent

from mcp import ClientSession
from tests.mcp.conftest import McpHarness
from tests.service.service_helpers import extracted, fetched_blocked, fetched_ok, normalized
from web_extractor.storage.database import Database
from web_extractor.storage.event_repository import EventRepository

_URL = "https://site.test/article"
_LONG_TEXT = "".join(f"Line {n:04d} of the page body. " for n in range(1000))  # ~28000 chars


def _text_of(result: CallToolResult) -> str:
    """All text content of a tool result."""
    return "\n".join(content.text for content in result.content if isinstance(content, TextContent))


@pytest.mark.asyncio
async def test_tools_list_shape_and_annotations(mcp: McpHarness) -> None:
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
    names = [tool.name for tool in tools]
    assert names == ["fetch_page", "read_more", "find_in_page", "check_urls"]
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True
        assert annotations.open_world_hint is True
    # Descriptions carry no variable data.
    for tool in tools:
        assert tool.description is not None and tool.description != ""


@pytest.mark.asyncio
async def test_fetch_page_truncates_and_read_more_completes(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(_LONG_TEXT, page_format="markdown"))

    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        first = await session.call_tool("fetch_page", {"url": _URL, "max_chars": 1000})
        text = _text_of(first)
        assert f"chars: 0-1000 of {len(_LONG_TEXT)} | TRUNCATED" in text
        assert "--- BEGIN UNTRUSTED PAGE CONTENT ---" in text
        assert first.is_error is False
        cursor = first.structured_content["cursor"]
        assert cursor is not None

        collected = _LONG_TEXT[:1000]
        while True:
            more = await session.call_tool("read_more", {"cursor": cursor})
            assert more.is_error is False
            meta = more.structured_content
            body = _text_of(more)
            start = body.index("--- BEGIN UNTRUSTED PAGE CONTENT ---\n") + len(
                "--- BEGIN UNTRUSTED PAGE CONTENT ---\n"
            )
            end = body.index("\n--- END UNTRUSTED PAGE CONTENT ---")
            collected += body[start:end]
            if not meta["truncated"]:
                assert "END OF TEXT" in body
                break
            cursor = meta["cursor"]
        assert collected == _LONG_TEXT


@pytest.mark.asyncio
async def test_unknown_cursor_error(mcp: McpHarness) -> None:
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("read_more", {"cursor": "zzzzzz"})
    assert result.is_error is True
    assert "ERROR unknown_cursor" in _text_of(result)
    assert result.structured_content["error_kind"] == "unknown_cursor"


@pytest.mark.asyncio
async def test_stale_cursor_after_fresh_refetch(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("version one text", page_format="markdown"))
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        first = await session.call_tool("fetch_page", {"url": _URL, "max_chars": 5})
        cursor = first.structured_content["cursor"]

        mcp.fetcher.script(fetched_ok(normalized(_URL)))
        mcp.extractor.script(extracted("version two text is different", page_format="markdown"))
        await session.call_tool("fetch_page", {"url": _URL, "max_chars": 5, "fresh": True})

        stale = await session.call_tool("read_more", {"cursor": cursor})
    assert stale.is_error is True
    assert "ERROR stale_cursor" in _text_of(stale)
    assert _URL in _text_of(stale)  # the URL is included so the agent can refetch


@pytest.mark.asyncio
async def test_find_in_page_verbatim_and_normalized(mcp: McpHarness) -> None:
    page_text = "She said \u201chello \u2014 world\u201d at dawn."
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted(page_text, page_format="text"))

    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        verbatim = await session.call_tool(
            "find_in_page", {"url": _URL, "query": "hello \u2014 world"}
        )
        fuzzy = await session.call_tool("find_in_page", {"url": _URL, "query": "hello - world"})
    verbatim_meta = verbatim.structured_content
    normalized_meta = fuzzy.structured_content
    assert verbatim_meta["matches"][0]["verbatim"] is True
    assert normalized_meta["matches"][0]["verbatim"] is False
    assert verbatim_meta["matches"][0]["offset"] == normalized_meta["matches"][0]["offset"]
    assert "hello" in _text_of(verbatim)


@pytest.mark.asyncio
async def test_find_in_page_no_matches_message(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("nothing relevant here", page_format="text"))

    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("find_in_page", {"url": _URL, "query": "absent phrase"})
    assert result.is_error is False
    text = _text_of(result)
    assert "matches: 0" in text
    assert "No match" in text
    assert result.structured_content["matches"] == []


@pytest.mark.asyncio
async def test_check_urls_order_and_summary(mcp: McpHarness) -> None:
    first_url = "https://a.test/one"
    second_url = "https://b.test/two"
    mcp.fetcher.script(fetched_ok(normalized(first_url)))
    mcp.extractor.script(extracted("alpha text", page_format="text"))
    mcp.fetcher.script(fetched_blocked(normalized(second_url)))

    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "check_urls", {"urls": [first_url, second_url], "fresh": True}
        )
    text = _text_of(result)
    assert "1. ok 200" in text
    assert "2. blocked 403" in text
    assert text.index(first_url) < text.index(second_url)
    assert "summary: 1 ok, 1 blocked" in text
    meta = result.structured_content
    assert meta["summary"] == "1 ok, 1 blocked"
    assert meta["url_rows"][0]["status"] == "ok"
    assert meta["url_rows"][1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_invalid_url_is_rejected_and_journaled(mcp: McpHarness) -> None:
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("fetch_page", {"url": "not-a-url"})
    assert result.is_error is True
    assert "ERROR invalid_argument" in _text_of(result)

    database = Database(mcp.settings.db_path)
    await database.connect()
    try:
        repo = EventRepository(database)
        assert await repo.count() == 1
        async with database.connection.execute(
            "SELECT tool, error_kind, status FROM events"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row["tool"] == "fetch_page"
        assert row["error_kind"] == "invalid_argument"
        assert row["status"] == "error"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_out_of_range_args_rejected_by_schema_without_journal(mcp: McpHarness) -> None:
    """The SDK validates the declared schema server-side; such protocol
    rejections return isError but never reach our journal (documented in
    docs/spikes/03_mcp_sdk.md)."""
    async with (
        streamable_http_client(mcp.url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("fetch_page", {"url": _URL, "max_chars": 0})
    assert result.is_error is True
    assert "max_chars" in _text_of(result)

    database = Database(mcp.settings.db_path)
    await database.connect()
    try:
        assert await EventRepository(database).count() == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_agent_id_from_header_when_param_absent(mcp: McpHarness) -> None:
    mcp.fetcher.script(fetched_ok(normalized(_URL)))
    mcp.extractor.script(extracted("some text", page_format="markdown"))
    http_client = create_mcp_http_client(headers={"X-Agent-Id": "header-agent"})

    async with (
        streamable_http_client(mcp.url, http_client=http_client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await session.call_tool("fetch_page", {"url": _URL})

    database = Database(mcp.settings.db_path)
    await database.connect()
    try:
        async with database.connection.execute("SELECT agent FROM events") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row["agent"] == "header-agent"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_healthz_and_readyz(mcp: McpHarness) -> None:
    base_url = mcp.url.rsplit("/mcp", 1)[0]
    async with AsyncClient(base_url=base_url) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"database": True}
