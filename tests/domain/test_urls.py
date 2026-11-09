"""Table-driven tests for URL parsing and normalization."""

import pytest

from web_extractor.domain.urls import (
    MAX_URL_LENGTH,
    NormalizedUrl,
    UrlParseError,
    parse_normalized_url,
)

CASES_VALID = [
    # (raw, tracking_params, expected normalized value)
    ("https://example.com", frozenset(), "https://example.com/"),
    ("http://Example.COM/Page", frozenset(), "http://example.com/Page"),
    ("https://example.com#fragment", frozenset(), "https://example.com/"),
    ("https://example.com/page?a=2&b=1", frozenset(), "https://example.com/page?a=2&b=1"),
    # Sorting is by parameter name (then value), so this one is reordered.
    ("https://example.com/page?b=2&a=1", frozenset(), "https://example.com/page?a=1&b=2"),
    (
        "https://example.com/?utm_source=rss&id=3&fbclid=x",
        frozenset({"utm_*", "fbclid", "gclid"}),
        "https://example.com/?id=3",
    ),
    (
        "https://example.com/?utm_campaign=a",
        frozenset({"utm_*", "fbclid"}),
        "https://example.com/",
    ),
    ("https://example.com:443/", frozenset(), "https://example.com/"),
    ("http://example.com:80/", frozenset(), "http://example.com/"),
    ("http://example.com:8080/", frozenset(), "http://example.com:8080/"),
    ("https://münchen.de/x", frozenset(), "https://xn--mnchen-3ya.de/x"),
    ("https://example.com./x", frozenset(), "https://example.com/x"),
    ("http://[2001:DB8::1]/x", frozenset(), "http://[2001:db8::1]/x"),
    ("http://[::1]:8080/x", frozenset(), "http://[::1]:8080/x"),
    ("https://example.com?a=1&", frozenset(), "https://example.com/?a=1"),
    ("https://example.com?b=&a=", frozenset(), "https://example.com/?a=&b="),
    # SSO-style params with special characters survive round-trip encoding.
    (
        "https://example.com/?next=https%3A%2F%2Fother.org%2F",
        frozenset(),
        "https://example.com/?next=https%3A%2F%2Fother.org%2F",
    ),
]

CASES_INVALID = [
    # (raw, expected failure reason)
    ("", "empty"),
    ("   ", "empty"),
    ("example.com/page", "no_scheme"),
    ("ftp://example.com/", "unsupported_scheme"),
    ("file:///etc/passwd", "unsupported_scheme"),
    ("https://user:pass@example.com/", "has_credentials"),
    ("https://token@example.com/", "has_credentials"),
    ("https:///", "missing_host"),
    ("https://exa mple.com/", "invalid_host"),
    ("https://under_score.example.com/", "invalid_host"),
    ("https://example.com:99999/", "invalid_port"),
    ("https://example.com:port/", "invalid_port"),
    ("https://[::1", "invalid_host"),
]


def _parsed(raw: str, tracking: frozenset[str] = frozenset()) -> NormalizedUrl:
    """Parse with the success branch asserted, for readable assertions."""
    result = parse_normalized_url(raw, tracking)
    assert isinstance(result, NormalizedUrl)
    return result


@pytest.mark.parametrize(("raw", "tracking", "expected"), CASES_VALID)
def test_valid_urls_normalize_to_expected_form(
    raw: str, tracking: frozenset[str], expected: str
) -> None:
    assert _parsed(raw, tracking).value == expected


@pytest.mark.parametrize(("raw", "reason"), CASES_INVALID)
def test_invalid_urls_rejected_with_reason(raw: str, reason: str) -> None:
    result = parse_normalized_url(raw)
    assert isinstance(result, UrlParseError)
    assert result.reason == reason
    assert result.message != ""


def test_normalized_url_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="parse_normalized_url"):
        NormalizedUrl("https://example.com/")


def test_url_over_limit_is_rejected() -> None:
    path_pad = len("https://example.com/")
    raw_at_limit = "https://example.com/" + "a" * (MAX_URL_LENGTH - path_pad)
    assert _parsed(raw_at_limit).value == raw_at_limit
    raw_over_limit = "https://example.com/" + "a" * (MAX_URL_LENGTH - path_pad + 1)
    rejected = parse_normalized_url(raw_over_limit)
    assert isinstance(rejected, UrlParseError)
    assert rejected.reason == "too_long"


def test_normalized_url_properties() -> None:
    parsed = _parsed("http://[::1]:8080/Path")
    assert parsed.scheme == "http"
    assert parsed.host == "::1"
    assert parsed.port == 8080


def test_port_property_none_when_default() -> None:
    assert _parsed("https://example.com/").port is None


def test_same_normalized_url_compares_equal() -> None:
    first = _parsed("https://example.com/a")
    second = _parsed("HTTPS://EXAMPLE.COM/a#top")
    assert first == second
    assert hash(first) == hash(second)
    assert str(first) == "https://example.com/a"
