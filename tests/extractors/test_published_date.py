"""Publication date from structured markup only (JSON-LD, meta, time[itemprop])."""

import pytest

from web_extractor.domain.extract import Extracted, PublishedSource
from web_extractor.extractors.published_date import find_published_date
from web_extractor.extractors.trafilatura_extractor import TrafilaturaExtractor

_LD = '<script type="application/ld+json">{body}</script>'


@pytest.mark.parametrize(
    ("html", "value", "source"),
    [
        (
            _LD.format(body='{"@type": "NewsArticle", "datePublished": "2026-03-01T10:00:00Z"}'),
            "2026-03-01T10:00:00+00:00",
            "json_ld",
        ),
        (
            _LD.format(
                body='{"@graph": [{"@type": "WebSite"}, '
                '{"@type": ["Article"], "datePublished": "2026-03-01"}]}'
            ),
            "2026-03-01",
            "json_ld",
        ),
        (
            '<meta property="article:published_time" content="2026-03-01T09:30:00+02:00">',
            "2026-03-01T09:30:00+02:00",
            "meta",
        ),
        ('<meta itemprop="datePublished" content="2026-03-01">', "2026-03-01", "meta"),
        (
            '<time itemprop="datePublished" datetime="2026-03-01">1 March</time>',
            "2026-03-01",
            "time_itemprop",
        ),
        # Same day from two sources: the higher-priority source is reported.
        (
            _LD.format(body='{"@type": "Article", "datePublished": "2026-03-01T08:00:00Z"}')
            + '<meta property="article:published_time" content="2026-03-01">',
            "2026-03-01T08:00:00+00:00",
            "json_ld",
        ),
    ],
)
def test_structured_sources(*, html: str, value: str, source: PublishedSource) -> None:
    found = find_published_date(html)

    assert found is not None
    assert (found.value, found.source) == (value, source)


@pytest.mark.parametrize(
    "html",
    [
        "<p>Published on January 1, 2026</p>",  # text only: never guessed
        '<time datetime="2026-01-01">Jan 1</time>',  # bare <time>: comments, sidebars
        '<meta property="article:modified_time" content="2026-03-02">',  # modification date
        _LD.format(body='{"@type": "Article", "dateModified": "2026-03-02"}'),
        _LD.format(body='{"@type": "WebSite", "datePublished": "2020-01-01"}'),  # not an article
        _LD.format(body='{"@type": "Article", "datePublished": "yesterday"}'),  # not a date
        _LD.format(body="{not json"),
        # Sources disagree on the day: no date beats a wrong one.
        _LD.format(body='{"@type": "Article", "datePublished": "2026-03-01"}')
        + '<meta property="article:published_time" content="2026-02-27">',
        # Two articles on one page (e.g. related posts) with different days.
        _LD.format(
            body='[{"@type": "Article", "datePublished": "2026-03-01"}, '
            '{"@type": "Article", "datePublished": "2025-12-31"}]'
        ),
    ],
)
def test_no_trustworthy_date_is_none(html: str) -> None:
    assert find_published_date(html) is None


@pytest.mark.asyncio
async def test_extractor_reports_structured_date() -> None:
    body = "<p>" + "A full paragraph of the article body, with enough words. " * 20 + "</p>"
    html = (
        "<html><head><title>Story</title>"
        '<meta property="article:published_time" content="2026-03-01T10:00:00Z">'
        f"</head><body><article>{body}</article></body></html>"
    ).encode()

    result = await TrafilaturaExtractor().extract(html, "https://news.test/story", "text")

    assert isinstance(result, Extracted)
    assert result.metadata.published_at == "2026-03-01T10:00:00+00:00"
    assert result.metadata.published_source == "meta"


@pytest.mark.asyncio
async def test_extractor_does_not_guess_from_text() -> None:
    body = "<p>" + "A full paragraph of the article body, with enough words. " * 20 + "</p>"
    html = (
        f"<html><body><article><p>Posted on January 1, 2026</p>{body}</article></body></html>"
    ).encode()

    result = await TrafilaturaExtractor().extract(html, "https://news.test/story", "text")

    assert isinstance(result, Extracted)
    assert result.metadata.published_at is None
    assert result.metadata.published_source is None
