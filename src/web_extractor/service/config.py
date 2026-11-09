"""Service-layer configuration as validated data.

The composition root (M5) builds this from environment settings; the
pipeline depends only on this frozen dataclass.
"""

from dataclasses import dataclass
from datetime import timedelta

_DEFAULT_TRACKING_PARAMS = ("utm_*", "fbclid", "gclid")
"""Query parameters dropped during URL normalization (01_requirements, 4.4)."""


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Pipeline tuning; every value is validated at startup by the config layer."""

    cache_ttl: timedelta = timedelta(hours=24)
    """How long successful entries stay fresh."""

    error_cache_ttl: timedelta = timedelta(hours=1)
    """How long error entries stay cached, so blocked sites are not retried."""

    tracking_params: tuple[str, ...] = _DEFAULT_TRACKING_PARAMS
    """URL query parameters stripped by normalization."""

    default_max_chars: int = 12000
    """MCP fetch_page default chunk size; the MCP layer enforces bounds."""
