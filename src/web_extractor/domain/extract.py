"""Types of the extraction stage: metadata, success, and expected failures."""

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

type DateSource = Literal["json_ld", "meta", "url", "text"]
"""Where the publication date was found (a heuristic hint, see
``extractors/date_source.py``)."""

type ExtractResult = Extracted | ExtractionFailure
"""Result of an extraction attempt: success or an expected failure value."""


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Metadata the extractor managed to find; every field may be absent."""

    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    """Publication date as provided by the extractor (ISO-like when present)."""
    published_source: DateSource | None = None
    """Provenance of ``published_at``; None exactly when the date is None."""
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
