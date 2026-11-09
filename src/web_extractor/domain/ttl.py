"""Cache TTL logic as pure functions over explicit time values.

Callers pass ``now`` from an injected ``Clock`` (see ``domain.types.Clock``);
nothing in this module reads a real clock, so TTL behavior is testable
deterministically.
"""

from datetime import datetime, timedelta


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
