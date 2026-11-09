"""Types of the fetch stage: request, success, and expected failures."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from web_extractor.domain.types import (
    AgentId,
    FetcherName,
    HttpStatus,
    PositiveInt,
    RenderMode,
    TaskId,
)
from web_extractor.domain.urls import NormalizedUrl

type FetchFailureKind = Literal[
    "blocked",
    "not_found",
    "timeout",
    "too_large",
    "network",
    "ssrf_blocked",
]
"""Expected fetch failures; ``blocked`` covers 403/429 and anti-bot walls."""

type FetchResult = Fetched | FetchFailure
"""Result of a fetch attempt: success or an expected failure value."""


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """What the pipeline asks a fetcher to do.

    ``fresh`` is keyword-facing only inside the service; here it is data, not
    a boolean function parameter.
    """

    url: NormalizedUrl
    fresh: bool
    render: RenderMode
    agent: AgentId
    task: TaskId | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """A successfully downloaded page.

    ``final_url`` is the URL after redirects, already normalized and
    SSRF-checked by the fetcher. ``redirect_chain`` lists intermediate URLs
    in order (without the final one). ``fetched_at`` comes from the clock
    injected into the fetcher.
    """

    final_url: NormalizedUrl
    http_status: HttpStatus
    headers: Mapping[str, str]
    body: bytes
    fetched_at: datetime
    fetcher: FetcherName
    redirect_chain: tuple[NormalizedUrl, ...] = ()


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """An expected fetch failure as a value, never an exception.

    ``http_status`` is present for kinds ``blocked`` and ``not_found`` and
    for size-limit rejections that the server answered with a status.
    ``retry_after_s`` is set from a ``Retry-After`` header on 429 responses.
    """

    kind: FetchFailureKind
    message: str
    http_status: HttpStatus | None = None
    final_url: NormalizedUrl | None = None
    retry_after_s: PositiveInt | None = None

    def __post_init__(self) -> None:
        if not self.message.strip():
            msg = "FetchFailure.message must not be empty"
            raise ValueError(msg)
        if self.kind in ("blocked", "not_found") and self.http_status is None:
            msg = f"kind {self.kind!r} requires http_status"
            raise ValueError(msg)
