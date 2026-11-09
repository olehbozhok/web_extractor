"""Fetcher implementations (HTTP now, browser later)."""

from web_extractor.fetchers.base import Fetcher, Resolver
from web_extractor.fetchers.browser_fetcher import BrowserFetcher, BrowserFetcherConfig
from web_extractor.fetchers.http_fetcher import (
    HttpFetcher,
    HttpFetcherConfig,
    PinnedNetworkBackend,
    PinnedTransport,
    SystemResolver,
)

__all__ = [
    "BrowserFetcher",
    "BrowserFetcherConfig",
    "Fetcher",
    "HttpFetcher",
    "HttpFetcherConfig",
    "PinnedNetworkBackend",
    "PinnedTransport",
    "Resolver",
    "SystemResolver",
]
