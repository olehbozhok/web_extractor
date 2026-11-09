"""Domain layer: pure types and rules, no I/O.

Imports from this package never reach for storage, fetchers or the network;
see docs/02_architecture.md section 2 for the layer contract.
"""

from web_extractor.domain.extract import (
    Extracted,
    ExtractionFailure,
    ExtractionFailureKind,
    ExtractResult,
    PageMetadata,
)
from web_extractor.domain.fetch import (
    Fetched,
    FetchFailure,
    FetchFailureKind,
    FetchRequest,
    FetchResult,
)
from web_extractor.domain.records import Event, PageRecord
from web_extractor.domain.ssrf import SsrfRule, SsrfViolation, check_address
from web_extractor.domain.ttl import cache_age, compute_expires_at, is_expired
from web_extractor.domain.types import (
    AGENT_ID_MAX_LENGTH,
    TASK_ID_MAX_LENGTH,
    AgentId,
    Clock,
    DetailLevel,
    ErrorKind,
    FetcherName,
    HttpStatus,
    NonEmptyStr,
    PageFormat,
    PositiveInt,
    RenderMode,
    Sha256Hex,
    Status,
    TaskId,
    ToolName,
)
from web_extractor.domain.urls import (
    MAX_URL_LENGTH,
    NormalizedUrl,
    UrlFailureReason,
    UrlParseError,
    parse_normalized_url,
)

__all__ = [
    "AGENT_ID_MAX_LENGTH",
    "MAX_URL_LENGTH",
    "TASK_ID_MAX_LENGTH",
    "AgentId",
    "Clock",
    "DetailLevel",
    "ErrorKind",
    "Event",
    "ExtractResult",
    "Extracted",
    "ExtractionFailure",
    "ExtractionFailureKind",
    "FetchFailure",
    "FetchFailureKind",
    "FetchRequest",
    "FetchResult",
    "Fetched",
    "FetcherName",
    "HttpStatus",
    "NonEmptyStr",
    "NormalizedUrl",
    "PageFormat",
    "PageMetadata",
    "PageRecord",
    "PositiveInt",
    "RenderMode",
    "Sha256Hex",
    "SsrfRule",
    "SsrfViolation",
    "Status",
    "TaskId",
    "ToolName",
    "UrlFailureReason",
    "UrlParseError",
    "cache_age",
    "check_address",
    "compute_expires_at",
    "is_expired",
    "parse_normalized_url",
]
