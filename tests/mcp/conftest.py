"""Integration harness for MCP tool tests: real server, real MCP client.

The app is wired through the real composition root (``create_app``) with
the fetcher and extractor substituted by scripted doubles; the MCP transport
is genuine (uvicorn on an ephemeral loopback port + SDK client).
"""

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
import uvicorn

from tests.service.service_helpers import FakeClock, ScriptedExtractor, ScriptedFetcher
from web_extractor.config import Settings
from web_extractor.mcp_api.app import create_app


@dataclass
class McpHarness:
    """Everything an MCP integration test needs."""

    url: str
    fetcher: ScriptedFetcher
    extractor: ScriptedExtractor
    clock: FakeClock
    settings: Settings
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest_asyncio.fixture
async def mcp(tmp_path: Path) -> AsyncIterator[McpHarness]:
    """Start the composed app on an ephemeral loopback port."""
    settings = Settings(
        db_path=str(tmp_path / "mcp.db"),
        per_host_max_concurrent=4,
        per_host_min_interval_s=0.0,
        global_max_concurrent=10,
    )
    fetcher = ScriptedFetcher()
    extractor = ScriptedExtractor()
    clock = FakeClock()
    app = create_app(settings, fetcher=fetcher, extractor=extractor, clock=clock)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread_errors: list[BaseException] = []

    def _run_server() -> None:
        try:
            server.run()
        except BaseException as error:  # surfaced by the startup wait below
            thread_errors.append(error)

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()

    started = threading.Event()

    def _wait_started() -> None:
        while not server.started:
            if thread_errors or not thread.is_alive():
                msg = f"uvicorn failed to start: {thread_errors}"
                raise RuntimeError(msg)
            time.sleep(0.01)  # in a worker thread: the event loop stays responsive
        started.set()

    await asyncio.to_thread(_wait_started)
    port = server.servers[0].sockets[0].getsockname()[1]
    harness = McpHarness(
        url=f"http://127.0.0.1:{port}/mcp",
        fetcher=fetcher,
        extractor=extractor,
        clock=clock,
        settings=settings,
        server=server,
        thread=thread,
    )
    yield harness
    harness.stop()
