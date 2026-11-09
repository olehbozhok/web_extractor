"""Date provenance heuristic and its integration with the HTML extractor."""

import pytest

from web_extractor.domain.extract import DateSource, Extracted
from web_extractor.extractors.date_source import find_date_source
from web_extractor.extractors.trafilatura_extractor import TrafilaturaExtractor

_URL = "https://news.test/story"


@pytest.mark.parametrize(
    ("html", "url", "expected"),
    [
        (
            '<script type="application/ld+json">{"datePublished": "2026-03-01"}</script>',
            _URL,
            "json_ld",
        ),
        ('<meta property="article:published_time" content="2026-03-01">', _URL, "meta"),
        ("<p>Posted yesterday</p>", "https://news.test/2026/03/01/story", "url"),
        ("<p>Published 1 March 2026</p>", _URL, "text"),
        ('"datePublished" and article:published_time', _URL, "json_ld"),
    ],
)
def test_source_classification(*, html: str, url: str, expected: DateSource) -> None:
    assert find_date_source(html, url, "2026-03-01") == expected


@pytest.mark.asyncio
async def test_extractor_reports_date_and_its_source() -> None:
    body = "<p>" + "A full paragraph of the article body, with enough words. " * 20 + "</p>"
    html = (
        "<html><head><title>Story</title>"
        '<meta property="article:published_time" content="2026-03-01T10:00:00Z">'
        f"</head><body><article>{body}</article></body></html>"
    ).encode()

    result = await TrafilaturaExtractor().extract(html, _URL, "text")

    assert isinstance(result, Extracted)
    assert result.metadata.published_at is not None
    assert result.metadata.published_at.startswith("2026-03-01")
    assert result.metadata.published_source == "meta"


@pytest.mark.asyncio
async def test_no_date_means_no_source() -> None:
    body = "<p>" + "A full paragraph of the article body, with enough words. " * 20 + "</p>"
    html = f"<html><body><article>{body}</article></body></html>".encode()

    result = await TrafilaturaExtractor().extract(html, _URL, "text")

    assert isinstance(result, Extracted)
    assert result.metadata.published_at is None
    assert result.metadata.published_source is None
