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

type ExtractResult = Extracted | ExtractionFailure
"""Result of an extraction attempt: success or an expected failure value."""


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Metadata the extractor managed to find; every field may be absent."""

    title: str | None = None
    author: str | None = None
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

    Keys that no longer exist (the publication date, removed because extractors
    guess it unreliably) are ignored, so old cache entries stay readable.
    """
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return PageMetadata()
    if not isinstance(data, dict):
        return PageMetadata()
    return PageMetadata(
        title=_opt_str(data, "title"),
        author=_opt_str(data, "author"),
        language=_opt_str(data, "language"),
        publisher=_opt_str(data, "publisher"),
    )


def _opt_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None
