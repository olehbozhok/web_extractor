"""HttpFetcher tests against a loopback server (S1, S5, S8 at fetcher level)."""

import time
from ipaddress import IPv4Address, IPv6Address

import pytest

from tests.fetchers.fetch_helpers import (
    FIXED_NOW,
    FailingResolver,
    FixedClock,
    LoopbackHttpServer,
    ScriptedResponse,
    StaticResolver,
)
from web_extractor.domain.fetch import Fetched, FetchFailure, FetchRequest
from web_extractor.domain.ssrf import SsrfViolation, check_address
from web_extractor.domain.types import AgentId
from web_extractor.domain.urls import NormalizedUrl, parse_normalized_url
from web_extractor.fetchers.http_fetcher import HttpFetcher, HttpFetcherConfig

_HOST = "site.test"

_OK_BODY = b"<html><body><h1>Hello</h1></body></html>"


def _allow_loopback_only(address: IPv4Address | IPv6Address) -> SsrfViolation | None:
    """SSRF rules with exactly one test accommodation: the loopback server.

    Everything else (private, reserved, NAT64, mapped, ports) behaves
    exactly like production.
    """
    if address.is_loopback:
        return None
    return check_address(address)


def _config(port: int) -> HttpFetcherConfig:
    return HttpFetcherConfig(
        user_agent="web-extractor-test/0.1",
        max_response_bytes=64_000,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        total_timeout_s=5.0,
        max_redirects=3,
        allowed_ports=(80, 443, port),
    )


def _fetcher(port: int, resolver: StaticResolver) -> HttpFetcher:
    return HttpFetcher(_config(port), FixedClock(FIXED_NOW), resolver, _allow_loopback_only)


def _request(url: str) -> FetchRequest:
    parsed = parse_normalized_url(url)
    assert isinstance(parsed, NormalizedUrl)
    return FetchRequest(url=parsed, fresh=False, render="static", agent=AgentId("tester"))


@pytest.mark.asyncio
async def test_successful_fetch_is_fetched() -> None:
    with LoopbackHttpServer(lambda _path, _headers: ScriptedResponse(200, {}, _OK_BODY)) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/")))
    assert isinstance(result, Fetched)
    assert result.body == _OK_BODY
    assert result.http_status.value == 200
    assert result.fetcher == "http"
    assert result.fetched_at == FIXED_NOW
    assert result.redirect_chain == ()


@pytest.mark.asyncio
async def test_blocked_403_and_429_are_value_failures() -> None:
    def handler(path: str, _headers: dict[str, str]) -> ScriptedResponse:
        if path == "/forbidden":
            return ScriptedResponse(403, {}, b"denied")
        return ScriptedResponse(429, {"Retry-After": "120"}, b"slow down")

    with LoopbackHttpServer(handler) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        blocked = await fetcher.fetch(_request(server.url_for(_HOST, "/forbidden")))
        throttled = await fetcher.fetch(_request(server.url_for(_HOST, "/throttled")))
    assert isinstance(blocked, FetchFailure)
    assert blocked.kind == "blocked"
    assert blocked.http_status is not None and blocked.http_status.value == 403
    assert blocked.retry_after_s is None
    assert isinstance(throttled, FetchFailure)
    assert throttled.kind == "blocked"
    assert throttled.retry_after_s is not None and throttled.retry_after_s.value == 120


@pytest.mark.asyncio
async def test_not_found_404() -> None:
    with LoopbackHttpServer(lambda _path, _headers: ScriptedResponse(404, {}, b"gone")) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/missing")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "not_found"


@pytest.mark.asyncio
async def test_server_error_500() -> None:
    with LoopbackHttpServer(lambda _path, _headers: ScriptedResponse(500, {}, b"boom")) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/broken")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "server_error"
    assert result.http_status is not None and result.http_status.value == 500


@pytest.mark.asyncio
async def test_redirect_chain_records_history() -> None:
    def handler(path: str, _headers: dict[str, str]) -> ScriptedResponse:
        if path == "/start":
            return ScriptedResponse(302, {"Location": "/final"}, b"")
        return ScriptedResponse(200, {}, _OK_BODY)

    with LoopbackHttpServer(handler) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/start")))
    assert isinstance(result, Fetched)
    assert result.final_url.value == server.url_for(_HOST, "/final")
    assert [hop.value for hop in result.redirect_chain] == [server.url_for(_HOST, "/start")]


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_ssrf_blocked() -> None:
    """S8: a redirect into a forbidden network must be rejected."""

    def handler(_path: str, _headers: dict[str, str]) -> ScriptedResponse:
        return ScriptedResponse(302, {"Location": "http://private.test:1/"}, b"")

    with LoopbackHttpServer(handler) as server:
        resolver = StaticResolver(
            {
                _HOST: [IPv4Address("127.0.0.1")],
                "private.test": [IPv4Address("10.0.0.1")],
            }
        )
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/redirect")))
        # The redirect target was never contacted.
        assert len(server.requests) == 1
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@pytest.mark.asyncio
async def test_private_ip_direct_is_ssrf_blocked() -> None:
    resolver = StaticResolver({_HOST: [IPv4Address("192.168.1.5")]})
    fetcher = _fetcher(80, resolver)
    result = await fetcher.fetch(_request(f"http://{_HOST}/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@pytest.mark.asyncio
async def test_mixed_public_and_private_records_fail_closed() -> None:
    """One forbidden address among several must reject the whole host."""
    resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1"), IPv4Address("10.9.9.9")]})
    fetcher = _fetcher(80, resolver)
    result = await fetcher.fetch(_request(f"http://{_HOST}/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"


@pytest.mark.asyncio
async def test_port_outside_allowlist_is_rejected_before_connecting() -> None:
    resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
    fetcher = _fetcher(80, resolver)
    result = await fetcher.fetch(_request(f"http://{_HOST}:9443/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "ssrf_blocked"
    assert resolver.hosts_resolved == []  # never even resolved


@pytest.mark.asyncio
async def test_too_large_body_is_rejected() -> None:
    big_body = b"x" * 1000

    with LoopbackHttpServer(lambda _path, _headers: ScriptedResponse(200, {}, big_body)) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        config = HttpFetcherConfig(
            user_agent="test",
            max_response_bytes=500,
            connect_timeout_s=2.0,
            read_timeout_s=2.0,
            total_timeout_s=5.0,
            max_redirects=3,
            allowed_ports=(80, 443, server.port),
        )
        fetcher = HttpFetcher(config, FixedClock(FIXED_NOW), resolver, _allow_loopback_only)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/big")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "too_large"


@pytest.mark.asyncio
async def test_slow_response_hits_total_timeout() -> None:
    def slow(_path: str, _headers: dict[str, str]) -> ScriptedResponse:
        time.sleep(1.0)
        return ScriptedResponse(200, {}, _OK_BODY)

    with LoopbackHttpServer(slow) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        config = HttpFetcherConfig(
            user_agent="test",
            max_response_bytes=64_000,
            connect_timeout_s=1.0,
            read_timeout_s=1.0,
            total_timeout_s=0.2,
            max_redirects=3,
            allowed_ports=(80, 443, server.port),
        )
        fetcher = HttpFetcher(config, FixedClock(FIXED_NOW), resolver, _allow_loopback_only)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/slow")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "timeout"


@pytest.mark.asyncio
async def test_redirect_loop_is_detected() -> None:
    def handler(_path: str, _headers: dict[str, str]) -> ScriptedResponse:
        return ScriptedResponse(302, {"Location": "/loop"}, b"")

    with LoopbackHttpServer(handler) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/loop")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "redirect_error"


@pytest.mark.asyncio
async def test_too_many_redirects_is_capped() -> None:
    def handler(path: str, _headers: dict[str, str]) -> ScriptedResponse:
        return ScriptedResponse(302, {"Location": f"/hop-{len(path)}"}, b"")

    with LoopbackHttpServer(handler) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/hop")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "redirect_error"


def _failing_fetcher() -> HttpFetcher:
    return HttpFetcher(_config(80), FixedClock(FIXED_NOW), FailingResolver(), _allow_loopback_only)


@pytest.mark.asyncio
async def test_dns_failure_is_dns_failure() -> None:
    fetcher = _failing_fetcher()
    result = await fetcher.fetch(_request(f"http://{_HOST}/"))
    assert isinstance(result, FetchFailure)
    assert result.kind == "dns"


@pytest.mark.asyncio
async def test_ipv6_pinned_connection_works() -> None:
    """A hostname pinned to ::1 must reach a server listening on ::1."""
    with LoopbackHttpServer(
        lambda _path, _headers: ScriptedResponse(200, {}, _OK_BODY), host="::1"
    ) as server:
        resolver = StaticResolver({_HOST: [IPv6Address("::1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/v6")))
    assert isinstance(result, Fetched)
    assert result.body == _OK_BODY


@pytest.mark.asyncio
async def test_unsupported_content_type_is_rejected_before_reading_the_body() -> None:
    with LoopbackHttpServer(
        lambda _path, _headers: ScriptedResponse(200, {"Content-Type": "application/zip"}, b"PK")
    ) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/archive.zip")))
    assert isinstance(result, FetchFailure)
    assert result.kind == "unsupported_type"
    assert result.http_status is not None and result.http_status.value == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream"])
async def test_pdf_and_generic_types_pass_through(content_type: str) -> None:
    with LoopbackHttpServer(
        lambda _path, _headers: ScriptedResponse(200, {"Content-Type": content_type}, b"%PDF-1.7")
    ) as server:
        resolver = StaticResolver({_HOST: [IPv4Address("127.0.0.1")]})
        fetcher = _fetcher(server.port, resolver)
        result = await fetcher.fetch(_request(server.url_for(_HOST, "/doc")))
    assert isinstance(result, Fetched)
    assert result.body == b"%PDF-1.7"
