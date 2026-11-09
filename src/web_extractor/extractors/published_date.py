"""Publication date from structured markup only.

Dates guessed from body text are wrong too often (a page without any date got
``2026-01-01``), and for a source card a wrong date is worse than none. So the
date is taken only where the site states it machine-readably:

1. JSON-LD ``datePublished`` on an article-like object (``Article``,
   ``NewsArticle``, ``BlogPosting``, ...);
2. ``<meta property="article:published_time">`` or
   ``<meta itemprop="datePublished">``;
3. ``<time itemprop="datePublished" datetime="...">``.

A bare ``<time datetime>`` is not used: pages carry many of them (comments,
"updated", related articles). Modification dates are never used. A value must
parse as an ISO 8601 date; when sources disagree on the calendar day the result
is None rather than a guess.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Final

from web_extractor.domain.extract import PublishedSource

_ARTICLE_TYPES: Final = frozenset(
    {
        "article",
        "newsarticle",
        "analysisnewsarticle",
        "opinionnewsarticle",
        "reportagenewsarticle",
        "backgroundnewsarticle",
        "reviewnewsarticle",
        "blogposting",
        "liveblogposting",
        "socialmediaposting",
        "discussionforumposting",
        "scholarlyarticle",
        "techarticle",
        "report",
    }
)
"""schema.org types whose ``datePublished`` is the publication of the page's content."""

_META_KEYS: Final = frozenset({"article:published_time", "datepublished"})
"""``property`` / ``itemprop`` values of meta tags that carry the publication date."""

_PRIORITY: Final[tuple[PublishedSource, ...]] = ("json_ld", "meta", "time_itemprop")


@dataclass(frozen=True, slots=True)
class PublishedDate:
    """A publication date (normalized ISO 8601) and where it was stated."""

    value: str
    source: PublishedSource


def find_published_date(html: str) -> PublishedDate | None:
    """The publication date stated in structured markup, or None."""
    collector = _Collector()
    try:
        collector.feed(html)
        collector.close()
    except AssertionError:
        # html.parser signals some malformed markup this way; keep what was read.
        pass
    candidates: list[tuple[PublishedSource, datetime | date]] = []
    for raw in collector.json_ld_blocks:
        candidates.extend(("json_ld", parsed) for parsed in _json_ld_dates(raw))
    candidates.extend(
        ("meta", parsed) for parsed in map(_parse_iso, collector.meta_values) if parsed
    )
    candidates.extend(
        ("time_itemprop", parsed) for parsed in map(_parse_iso, collector.time_values) if parsed
    )
    if not candidates:
        return None
    days = {_day(parsed) for _source, parsed in candidates}
    if len(days) != 1:
        return None  # sources disagree: no date beats a wrong date
    for source in _PRIORITY:
        for candidate_source, parsed in candidates:
            if candidate_source == source:
                return PublishedDate(value=parsed.isoformat(), source=source)
    return None  # unreachable: every candidate has a source from _PRIORITY


class _Collector(HTMLParser):
    """Collects JSON-LD blocks, publication meta values and time[itemprop] values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_ld_blocks: list[str] = []
        self.meta_values: list[str] = []
        self.time_values: list[str] = []
        self._in_json_ld = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "script" and attributes.get("type", "").strip().lower() == (
            "application/ld+json"
        ):
            self._in_json_ld = True
            self._buffer = []
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("itemprop") or "").lower()
            content = attributes.get("content")
            if key in _META_KEYS and content:
                self.meta_values.append(content)
        elif tag == "time" and attributes.get("itemprop", "").lower() == "datepublished":
            value = attributes.get("datetime")
            if value:
                self.time_values.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self.json_ld_blocks.append("".join(self._buffer))
            self._in_json_ld = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._buffer.append(data)


def _json_ld_dates(raw: str) -> list[datetime | date]:
    """``datePublished`` of every article-like object in one JSON-LD block."""
    try:
        document: object = json.loads(raw)
    except json.JSONDecodeError:
        return []
    found: list[datetime | date] = []
    stack: list[object] = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        graph = node.get("@graph")
        if graph is not None:
            stack.append(graph)
        if _is_article(node.get("@type")):
            published = node.get("datePublished")
            parsed = _parse_iso(published) if isinstance(published, str) else None
            if parsed is not None:
                found.append(parsed)
    return found


def _is_article(type_value: object) -> bool:
    types = type_value if isinstance(type_value, list) else [type_value]
    return any(isinstance(item, str) and item.lower() in _ARTICLE_TYPES for item in types)


def _parse_iso(raw: str) -> datetime | date | None:
    """An ISO 8601 date or date-time; anything else is None."""
    text = raw.strip()
    try:
        if len(text) == len("YYYY-MM-DD"):
            return date.fromisoformat(text)
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _day(value: datetime | date) -> date:
    """The calendar day as stated (no timezone conversion)."""
    return value.date() if isinstance(value, datetime) else value
