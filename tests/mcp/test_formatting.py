"""Error advice, render-mode retries and non-BMP sanitization of responses."""

import pytest

from web_extractor.domain.types import ErrorKind, FetcherName, RenderMode
from web_extractor.mcp_api.formatting import (
    error_text,
    retry_render,
    sanitize_json_value,
    sanitize_non_bmp,
)


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


def test_sanitize_non_bmp_replaces_only_astral_characters() -> None:
    assert sanitize_non_bmp("ok \U0001f996 end") == "ok \ufffd end"
    assert sanitize_non_bmp("") == ""
    # ASCII, Cyrillic and BMP characters (punctuation, symbols) stay as they are.
    assert sanitize_non_bmp("Кириллиця ± ™ — \uffff") == "Кириллиця ± ™ — \uffff"


def test_sanitize_json_value_walks_nested_structures() -> None:
    value: dict[str, object] = {
        "name": "link \U0001f517",
        "rows": [{"text": "row \U0001f9a0"}, "plain"],
        "count": 2,
        "missing": None,
    }

    sanitized = sanitize_json_value(value)

    assert sanitized == {
        "name": "link \ufffd",
        "rows": [{"text": "row \ufffd"}, "plain"],
        "count": 2,
        "missing": None,
    }
