"""Where a publication date came from: a heuristic over the raw HTML.

The extractor hands back a date but not its provenance. For citation work the
provenance matters: a ``datePublished`` field is a far stronger signal than a
date guessed from body text. The result is a hint, never a guarantee.
"""

from typing import Final

from web_extractor.domain.extract import DateSource

_JSON_LD_MARKERS: Final = ('"datepublished"', "'datepublished'")
_DATE_PARTS: Final = 3
"""Year, month and day."""

_META_MARKERS: Final = (
    "article:published_time",
    "og:article:published",
    'itemprop="datepublished"',
    "itemprop='datepublished'",
    'name="pubdate"',
    'name="publishdate"',
    'name="date"',
    'name="dc.date',
)


def find_date_source(html: str, final_url: str, published_at: str) -> DateSource | None:
    """Classify the provenance of ``published_at`` (ISO-like date text).

    None means there is no evidence the date is a publication date (it was
    guessed from body text): the caller must discard it."""
    lowered = html.lower()
    if any(marker in lowered for marker in _JSON_LD_MARKERS):
        return "json_ld"
    if any(marker in lowered for marker in _META_MARKERS):
        return "meta"
    if _date_in_url(final_url, published_at):
        return "url"
    return None


def _date_in_url(url: str, published_at: str) -> bool:
    """True when the URL path carries the date's year/month/day parts."""
    parts = published_at[:10].split("-")
    if len(parts) != _DATE_PARTS or not all(part.isdigit() for part in parts):
        return False
    year, month, day = parts
    return f"{year}/{month}/{day}" in url or f"{year}-{month}-{day}" in url
