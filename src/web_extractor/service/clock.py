"""Concrete clock implementation for production wiring."""

from datetime import UTC, datetime


class SystemClock:
    """Reads the real UTC time; logic never calls datetime.now() itself."""

    def now(self) -> datetime:
        """The current aware UTC datetime."""
        return datetime.now(tz=UTC)
