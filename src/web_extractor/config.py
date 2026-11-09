"""Application configuration via pydantic-settings (environment variables).

All values carry bounds so an invalid configuration fails loudly at
startup, before any request is served (01_requirements.md, 4.8). Variables
use the ``WX_`` prefix; complex values (lists, mappings) are JSON.
"""

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_TRACKING_PARAMS = ("utm_*", "fbclid", "gclid")


class DomainLimitOverride(BaseModel):
    """Per-host limit overrides from ``WX_LIMIT_OVERRIDES``."""

    max_concurrent: int = Field(ge=1, le=100)
    min_interval_s: float = Field(ge=0.0, le=3600.0)


class Settings(BaseSettings):
    """Everything the composition root needs to build the service."""

    model_config = SettingsConfigDict(env_prefix="WX_", env_file=None, extra="forbid")

    host: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1"
    """Bind address. ``0.0.0.0`` exists only for containers, where compose
    publishes the port on the host's 127.0.0.1; outside Docker keep the
    default so the service never listens on external interfaces."""

    port: int = Field(default=8321, ge=1, le=65535)

    db_path: str = Field(default="data/web_extractor.db", min_length=1)

    cache_ttl_hours: float = Field(default=24.0, gt=0.0, le=24.0 * 30)
    error_cache_ttl_hours: float = Field(default=1.0, gt=0.0, le=24.0 * 7)
    cursor_ttl_hours: float = Field(default=24.0, gt=0.0, le=24.0 * 7)

    tracking_params: tuple[str, ...] = _DEFAULT_TRACKING_PARAMS
    """Query parameters dropped by URL normalization; JSON in the env var."""

    user_agent: str = Field(default="web-extractor/0.1 (local research service)", min_length=1)

    max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    connect_timeout_s: float = Field(default=10.0, gt=0.0, le=300.0)
    read_timeout_s: float = Field(default=30.0, gt=0.0, le=600.0)
    total_timeout_s: float = Field(default=60.0, gt=0.0, le=600.0)
    max_redirects: int = Field(default=10, ge=1, le=50)
    allowed_ports: tuple[int, ...] = (80, 443, 8080, 8443)

    per_host_max_concurrent: int = Field(default=2, ge=1, le=100)
    per_host_min_interval_s: float = Field(default=1.0, ge=0.0, le=3600.0)
    global_max_concurrent: int = Field(default=20, ge=1, le=1000)
    limit_overrides: dict[str, DomainLimitOverride] = Field(default_factory=dict)
    """Hostname -> limits; JSON in the env var."""

    browser_base_url: str = ""
    """Crawl4AI base URL on core_net; empty disables browser rendering."""
    browser_token: str = ""
    """Bearer token from the environment; empty disables browser rendering."""
    browser_page_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    browser_max_concurrent: int = Field(default=3, ge=1, le=10)
    """Must stay below Crawl4AI's pool.max_pages (docs/spikes/02, appendix)."""

    default_max_chars: int = Field(default=12000, ge=1, le=50000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    def base_url(self) -> str:
        """The loopback base URL the server listens on."""
        return f"http://{self.host}:{self.port}"
