"""The real ToolClient: drives the actual service over MCP.

A fixture fetcher serves the deterministic evaluation pages; everything
else (pipeline, cache, tools, formatting) is the shipped code, so harness
measurements describe the real system.
"""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from evals.harness import ToolOutcome
from evals.pages import BLOCKED_URL, FIXTURE_PAGES, GONE_URL
from mcp import ClientSession
from web_extractor.config import Settings
from web_extractor.domain.fetch import Fetched, FetchFailure, FetchRequest, FetchResult
from web_extractor.domain.types import HttpStatus
from web_extractor.extractors.trafilatura_extractor import TrafilaturaExtractor
from web_extractor.mcp_api.app import create_app


class FixtureFetcher:
    """Serves the deterministic evaluation pages instead of the network."""

    async def fetch(self, request: FetchRequest) -> FetchResult:
        url = request.url.value
        html = FIXTURE_PAGES.get(url)
        if url == BLOCKED_URL:
            return FetchFailure(
                "blocked", "403 forbidden", http_status=HttpStatus(403), final_url=request.url
            )
        if url == GONE_URL:
            return FetchFailure(
                "not_found", "404 not found", http_status=HttpStatus(404), final_url=request.url
            )
        if html is None:
            return FetchFailure("network", f"no fixture for {url}", final_url=request.url)
        return Fetched(
            final_url=request.url,
            http_status=HttpStatus(200),
            headers={},
            body=html.encode("utf-8"),
            fetched_at=datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC),
            fetcher="http",
        )


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as advertised over MCP: name, description, JSON input schema."""

    name: str
    description: str
    input_schema: dict[str, object]


class McpToolClient:
    """ToolClient over a real MCP session against the composed app."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def specs(self) -> tuple[ToolSpec, ...]:
        """The tools exactly as the server advertises them to a model."""
        listed = await self._session.list_tools()
        return tuple(
            ToolSpec(tool.name, tool.description or "", dict(tool.input_schema))
            for tool in listed.tools
        )

    async def call(self, tool: str, arguments: Mapping[str, object]) -> ToolOutcome:
        result = await self._session.call_tool(tool, dict(arguments))
        text = "\n".join(
            content.text for content in result.content if isinstance(content, TextContent)
        )
        meta = result.structured_content if isinstance(result.structured_content, dict) else {}
        return ToolOutcome(text=text, meta=meta, is_error=result.is_error)


class McpToolClientFactory:
    """Opens one MCP session per agent run; sessions are independent."""

    def __init__(self, url: str) -> None:
        self._url = url

    def url(self) -> str:
        """The MCP endpoint."""
        return self._url

    @asynccontextmanager
    async def client(self) -> AsyncIterator[McpToolClient]:
        async with (
            streamable_http_client(self._url) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield McpToolClient(session)


@asynccontextmanager
async def evaluation_server(
    tmp_path: Path,
) -> AsyncIterator[tuple[str, McpToolClientFactory]]:
    """Start the composed app with fixture pages on an ephemeral port.

    Yields the MCP endpoint URL and a factory producing a fresh MCP session
    per agent run, so runs do not share session state.
    """
    settings = Settings(
        db_path=str(tmp_path / "eval.db"),
        per_host_max_concurrent=4,
        per_host_min_interval_s=0.0,
        global_max_concurrent=10,
    )
    app = create_app(
        settings, fetcher=FixtureFetcher(), extractor=TrafilaturaExtractor(), clock=RealClock()
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def _wait_started() -> None:

        while not server.started:
            time.sleep(0.01)  # worker thread: the event loop stays responsive

    # Wait through a worker thread so the loop keeps serving nothing extra.
    await asyncio.to_thread(_wait_started)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    factory = McpToolClientFactory(url)
    try:
        yield url, factory
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class RealClock:
    """Wall clock: the evaluation measures real durations (not the pipeline)."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
