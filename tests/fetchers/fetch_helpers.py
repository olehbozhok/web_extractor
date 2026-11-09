"""Helpers for fetcher tests: loopback HTTP server, fake resolver, fake clock.

Real network access is forbidden in tests (AGENTS.md section 11); the server
listens on ``127.0.0.1`` with an ephemeral port, and ``StaticResolver`` points
an unresolvable fake hostname at it — which also exercises the fetcher's
pinned-connection path end to end.
"""

import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address
from typing import Self


@dataclass(frozen=True, slots=True)
class ScriptedResponse:
    """What the scripted handler answers with."""

    status: int
    headers: Mapping[str, str]
    body: bytes


Handler = Callable[[str, dict[str, str]], ScriptedResponse]
"""Route handler: (path, request headers) -> response."""


class LoopbackHttpServer:
    """Threaded loopback HTTP server with a per-test handler function.

    Binds IPv4 (``127.0.0.1``) or IPv6 (``::1``) depending on ``host``.
    """

    def __init__(self, handler: Handler, host: str = "127.0.0.1") -> None:
        self._handler = handler
        self.requests: list[str] = []
        server = self

        class _HandlerClass(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                server.requests.append(self.path)
                response = server._handler(self.path, dict(self.headers))
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                self.wfile.write(response.body)

            def log_message(self, *args: object) -> None:
                pass  # keep test output clean

        if ":" in host:

            class _ServerClass(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            server_class: type[ThreadingHTTPServer] = _ServerClass
        else:
            server_class = ThreadingHTTPServer
        self._server = server_class((host, 0), _HandlerClass)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def port(self) -> int:
        """The ephemeral port the server listens on."""
        return self._server.server_address[1]

    def url_for(self, host: str, path: str) -> str:
        """Absolute URL with the fake host and the real port."""
        return f"http://{host}:{self.port}{path}"


class StaticResolver:
    """Resolver with a fixed host->addresses table; records lookup order."""

    def __init__(self, table: Mapping[str, Sequence[IPv4Address | IPv6Address]]) -> None:
        self._table = dict(table)
        self.hosts_resolved: list[str] = []

    async def resolve(self, host: str) -> tuple[IPv4Address | IPv6Address, ...]:
        self.hosts_resolved.append(host)
        addresses = self._table.get(host)
        if addresses is None:
            raise socket.gaierror(-2, f"Name or service not known: {host!r}")
        return tuple(addresses)


class FailingResolver:
    """Resolver that always fails with ``socket.gaierror``."""

    async def resolve(self, host: str) -> tuple[IPv4Address | IPv6Address, ...]:
        raise socket.gaierror(-2, f"Name or service not known: {host!r}")


@dataclass(frozen=True, slots=True)
class FixedClock:
    """Clock returning one fixed UTC instant."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


FIXED_NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
"""Shared fake instant for fetcher tests."""
