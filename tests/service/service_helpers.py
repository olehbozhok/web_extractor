"""Shared test doubles for service-layer tests: fake clock, fetcher, extractor."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from web_extractor.domain.extract import Extracted, ExtractResult, PageMetadata
from web_extractor.domain.fetch import Fetched, FetchFailure, FetchRequest, FetchResult
from web_extractor.domain.types import HttpStatus
from web_extractor.domain.urls import NormalizedUrl, parse_normalized_url


def normalized(raw: str) -> NormalizedUrl:
    """Parse with the success branch asserted, for building fixtures."""
    parsed = parse_normalized_url(raw)
    assert isinstance(parsed, NormalizedUrl)
    return parsed


class FakeClock:
    """Clock the tests advance manually."""

    def __init__(self, moment: datetime = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def advance(self, seconds: float) -> None:
        self._moment = self._moment + timedelta(seconds=seconds)


class ScriptedFetcher:
    """Returns scripted results in order; records calls; optional gate.

    When ``gated`` is armed, ``fetch`` blocks on ``release`` so tests can
    stage concurrent requests deterministically without sleeping.
    """

    def __init__(self) -> None:
        self.calls: list[FetchRequest] = []
        self._results: list[FetchResult] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.gated = False

    def script(self, *results: FetchResult) -> None:
        self._results.extend(results)

    async def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request)
        self.entered.set()
        if self.gated:
            await self.release.wait()
        return self._results.pop(0)


class ScriptedExtractor:
    """Returns scripted extraction results in order; records inputs."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []
        self._results: list[ExtractResult] = []

    def script(self, *results: ExtractResult) -> None:
        self._results.extend(results)

    async def extract(self, html: bytes, final_url: str, _page_format: str) -> ExtractResult:
        self.calls.append((html, final_url))
        return self._results.pop(0)


def fetched_ok(url: NormalizedUrl, body: bytes = b"<html><body>content</body></html>") -> Fetched:
    """A Fetched value for the given URL."""
    return Fetched(
        final_url=url,
        http_status=HttpStatus(200),
        headers={},
        body=body,
        fetched_at=datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC),
        fetcher="http",
    )


def fetched_blocked(url: NormalizedUrl) -> FetchFailure:
    return FetchFailure("blocked", "403 forbidden", http_status=HttpStatus(403), final_url=url)


def sha_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def extracted(
    text: str = "Main page text.", page_format: Literal["markdown", "text"] = "text"
) -> Extracted:
    return Extracted(text=text, format=page_format, metadata=PageMetadata(title="Title"))
