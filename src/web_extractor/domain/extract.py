"""Types of the extraction stage: metadata, success, and expected failures."""

import json
from dataclasses import dataclass
from typing import Literal

from web_extractor.domain.types import PageFormat

type ExtractionFailureKind = Literal[
    "empty",
    "needs_js",
    "parse_error",
]
"""``empty``: no content found; ``needs_js``: markers of a JS-only page;
``parse_error``: HTML could not be processed."""

type PublishedSource = Literal["json_ld", "meta", "time_itemprop"]
"""Structured markup the publication date was read from
(``extractors/published_date.py``); nothing else is trusted."""

type ExtractResult = Extracted | ExtractionFailure
"""Result of an extraction attempt: success or an expected failure value."""


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Metadata the extractor managed to find; every field may be absent."""

    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    """ISO 8601 publication date from structured markup only; None when not stated."""
    published_source: PublishedSource | None = None
    """Where ``published_at`` was stated; None exactly when the date is None."""
    language: str | None = None
    publisher: str | None = None


@dataclass(frozen=True, slots=True)
class Extracted:
    """Extracted page content in one requested format.

    ``text`` is guaranteed non-blank: an extractor that finds nothing must
    return :class:`ExtractionFailure` instead of empty text — an empty body
    with status ``ok`` is an invalid pipeline result.
    """

    text: str
    format: PageFormat
    metadata: PageMetadata

    def __post_init__(self) -> None:
        if not self.text.strip():
            msg = "Extracted.text must not be empty; return ExtractionFailure instead"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ExtractionFailure:
    """An expected extraction failure as a value, never an exception."""

    kind: ExtractionFailureKind
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            msg = "ExtractionFailure.message must not be empty"
            raise ValueError(msg)


def parse_metadata_json(raw: str) -> PageMetadata:
    """Parse stored metadata JSON; anything malformed degrades to empty.

    A stored date is kept only together with a known structured source: dates
    cached by older versions (guessed from text) are dropped.
    """
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return PageMetadata()
    if not isinstance(data, dict):
        return PageMetadata()
    source = _published_source(data)
    return PageMetadata(
        title=_opt_str(data, "title"),
        author=_opt_str(data, "author"),
        published_at=_opt_str(data, "published_at") if source is not None else None,
        published_source=source if _opt_str(data, "published_at") is not None else None,
        language=_opt_str(data, "language"),
        publisher=_opt_str(data, "publisher"),
    )


def _published_source(data: dict[str, object]) -> PublishedSource | None:
    match data.get("published_source"):
        case "json_ld":
            return "json_ld"
        case "meta":
            return "meta"
        case "time_itemprop":
            return "time_itemprop"
        case _:
            return None


def _opt_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None
