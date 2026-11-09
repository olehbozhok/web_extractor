"""Entry point: ``uv run python -m web_extractor``.

Loads Settings (failing loudly on invalid configuration), configures JSON
logging, and serves on loopback only.
"""

import json
import logging
import sys
from datetime import UTC, datetime

import uvicorn

from web_extractor.config import Settings
from web_extractor.mcp_api.app import create_app


class JsonLogFormatter(logging.Formatter):
    """One JSON object per log line (01_requirements.md, section 4.8)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def main() -> int:
    """Load settings and run the server; invalid config exits with code 2."""
    try:
        settings = Settings()
    except Exception as error:
        print(f"invalid configuration: {error}", file=sys.stderr)  # noqa: T201 - startup failure
        return 2
    _configure_logging(settings.log_level)
    logging.getLogger("web_extractor.app").info("starting web_extractor on %s", settings.base_url())
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
