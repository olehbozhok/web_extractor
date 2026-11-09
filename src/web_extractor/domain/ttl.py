"""Cache TTL logic and UTC timestamp helpers as pure functions.

Callers pass ``now`` from an injected ``Clock`` (see ``domain.types.Clock``);
nothing in this module reads a real clock, so TTL behavior is testable
deterministically. Timestamps stored in the database are ISO 8601 UTC
strings; within one format they compare correctly as plain strings, which
the statistics module relies on.
"""

from datetime import UTC, datetime, timedelta


def is_expired(expires_at: datetime, now: datetime) -> bool:
    """True when a cache entry must not be served anymore.

    An entry is stale exactly at ``expires_at`` and later: the boundary
    belongs to the next fetch, not to the current one.
    """
    return now >= expires_at


def compute_expires_at(fetched_at: datetime, ttl: timedelta) -> datetime:
    """Expiry timestamp for an entry fetched at ``fetched_at``."""
    return fetched_at + ttl


def cache_age(now: datetime, fetched_at: datetime) -> timedelta:
    """How long ago the entry was fetched; callers keep ``now >= fetched_at``."""
    return now - fetched_at


def to_iso_utc(moment: datetime) -> str:
    """Format an aware datetime as an ISO 8601 UTC string for storage."""
    if moment.tzinfo is None:
        msg = "naive datetime is forbidden; pass an aware UTC datetime"
        raise ValueError(msg)
    return moment.astimezone(UTC).isoformat()


def parse_iso_utc(text: str) -> datetime:
    """Parse a stored ISO 8601 timestamp; naive timestamps are forbidden."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        msg = f"stored timestamp {text!r} has no timezone"
        raise ValueError(msg)
    return parsed
