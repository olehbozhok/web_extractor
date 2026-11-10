"""Error advice and render-mode retries of the response formatter."""

import pytest

from web_extractor.domain.types import ErrorKind, FetcherName, RenderMode
from web_extractor.mcp_api.formatting import error_text, retry_render


@pytest.mark.parametrize(
    ("kind", "fetcher", "expected"),
    [
        ("blocked", "browser", "static"),
        ("timeout", "browser", "static"),
        ("network", "browser", "static"),
        ("server_error", "browser", "static"),
        ("browser_unavailable", "browser", "static"),
        ("blocked", "http", "browser"),
        ("needs_browser", "http", "browser"),
        ("needs_browser", "browser", None),
        ("empty_render", "browser", None),
        ("not_found", "browser", None),
        ("not_found", "http", None),
        ("dns", "browser", None),
        ("ssrf_blocked", "browser", None),
        ("timeout", "http", None),
        ("network", "http", None),
        ("blocked", None, None),
        ("invalid_argument", None, None),
    ],
)
def test_retry_render_switches_to_the_other_fetcher(
    kind: ErrorKind, fetcher: FetcherName | None, expected: RenderMode | None
) -> None:
    assert retry_render(kind, fetcher) == expected


def test_browser_block_advises_static_retry() -> None:
    text = error_text("blocked", "403 through the browser", "browser")
    assert text.startswith("ERROR blocked: 403 through the browser. ")
    assert "render=static" in text
    assert "render=browser" not in text


def test_static_block_advises_browser_retry() -> None:
    text = error_text("blocked", "403 forbidden", "http")
    assert "render=browser" in text
    assert "render=static" not in text


def test_browser_navigation_failure_mentions_downloads() -> None:
    text = error_text("network", "browser could not load x: net::ERR_FAILED", "browser")
    assert "render=static" in text
    assert "PDF" in text


def test_failure_without_fetcher_gives_no_render_advice() -> None:
    text = error_text("not_found", "404", None)
    assert "render=" not in text


def test_empty_render_advises_another_source_without_a_retry() -> None:
    """The browser already rendered: a render-mode retry would loop."""
    text = error_text("empty_render", "no text after rendering", "browser")
    assert text.startswith("ERROR empty_render: no text after rendering. ")
    assert "another source" in text
    assert "render=" not in text
