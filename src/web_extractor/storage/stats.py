"""Aggregated statistics over the event journal, for the web dashboard.

Period-bounded aggregates run in one table scan with the heavy work
(domain extraction, percentiles, tops) done in Python: the local journal
is small, SQLite lacks percentile functions, and the logic stays testable.
"""

import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from web_extractor.storage.database import Database
from web_extractor.storage.rows import (
    as_bool,
    as_int,
    as_opt_fetcher,
    as_opt_int,
    as_opt_str,
    as_status,
    as_str,
    row_value,
)

DEFAULT_TOP_LIMIT = 10
"""Entries shown per top-list on the dashboard."""


@dataclass(frozen=True, slots=True)
class NamedCount:
    """A name with its event count, ready for templates."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class PeriodStats:
    """Dashboard aggregates for one time period."""

    total_events: int
    cache_hits: int
    by_status: dict[str, int]
    by_http_status: dict[str, int]
    by_fetcher: dict[str, int]
    needs_browser_count: int
    unknown_agent_count: int
    top_agents: tuple[NamedCount, ...]
    top_tasks: tuple[NamedCount, ...]
    top_domains: tuple[NamedCount, ...]
    top_urls: tuple[NamedCount, ...]
    duration_p50_ms: int | None
    duration_p95_ms: int | None


@dataclass(frozen=True, slots=True)
class _RawEvent:
    """Minimal projection of one event row for aggregation."""

    agent: str
    task: str | None
    url: str
    status: str
    http_status: int | None
    cache_hit: bool
    fetcher: str | None
    duration_ms: int


async def collect_period_stats(
    database: Database,
    *,
    since_iso: str,
    until_iso: str,
    top_limit: int = DEFAULT_TOP_LIMIT,
) -> PeriodStats:
    """Aggregate events with ``since_iso <= ts <= until_iso``.

    Timestamps are compared as ISO 8601 UTC strings, which orders
    lexicographically (AGENTS.md section 9); callers pass strings produced
    by the same formatting used when appending events.
    """
    query = """
        SELECT agent, task, url, status, http_status, cache_hit, fetcher,
               duration_ms
        FROM events
        WHERE ts >= ? AND ts <= ?
    """
    async with database.connection.execute(query, (since_iso, until_iso)) as cursor:
        rows = await cursor.fetchall()
    events = [_parse_event_row(row) for row in rows]
    return _aggregate(events, top_limit)


def _parse_event_row(row: sqlite3.Row) -> _RawEvent:
    """Narrow one raw event row."""
    return _RawEvent(
        agent=as_str(row_value(row, "agent"), "agent"),
        task=as_opt_str(row_value(row, "task"), "task"),
        url=as_str(row_value(row, "url"), "url"),
        status=as_status(row_value(row, "status"), "status"),
        http_status=as_opt_int(row_value(row, "http_status"), "http_status"),
        cache_hit=as_bool(row_value(row, "cache_hit"), "cache_hit"),
        fetcher=as_opt_fetcher(row_value(row, "fetcher"), "fetcher"),
        duration_ms=as_int(row_value(row, "duration_ms"), "duration_ms"),
    )


def _aggregate(events: list[_RawEvent], top_limit: int) -> PeriodStats:
    """Pure aggregation: counters, tops, percentiles."""
    status_counter: Counter[str] = Counter()
    http_status_counter: Counter[str] = Counter()
    fetcher_counter: Counter[str] = Counter()
    agent_counter: Counter[str] = Counter()
    task_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()
    url_counter: Counter[str] = Counter()
    durations: list[int] = []
    cache_hits = 0
    needs_browser_count = 0
    unknown_agent_count = 0

    for event in events:
        status_counter[event.status] += 1
        http_status_counter[_http_status_key(event.http_status)] += 1
        if event.fetcher is not None:
            fetcher_counter[event.fetcher] += 1
        agent_counter[event.agent] += 1
        if event.task is not None:
            task_counter[event.task] += 1
        domain_counter[_domain_of(event.url)] += 1
        url_counter[event.url] += 1
        durations.append(event.duration_ms)
        if event.cache_hit:
            cache_hits += 1
        if event.status == "needs_browser":
            needs_browser_count += 1
        if event.agent == "unknown":
            unknown_agent_count += 1

    return PeriodStats(
        total_events=len(events),
        cache_hits=cache_hits,
        by_status=_sorted_counts(status_counter),
        by_http_status=_sorted_counts(http_status_counter),
        by_fetcher=_sorted_counts(fetcher_counter),
        needs_browser_count=needs_browser_count,
        unknown_agent_count=unknown_agent_count,
        top_agents=_top(agent_counter, top_limit),
        top_tasks=_top(task_counter, top_limit),
        top_domains=_top(domain_counter, top_limit),
        top_urls=_top(url_counter, top_limit),
        duration_p50_ms=_nearest_rank(durations, 50),
        duration_p95_ms=_nearest_rank(durations, 95),
    )


def _http_status_key(http_status: int | None) -> str:
    """Bucket key for the HTTP status distribution; missing codes bucket as ``none``."""
    return "none" if http_status is None else str(http_status)


def _domain_of(url: str) -> str:
    """The host part of a normalized URL; empty string when absent."""
    hostname = urlsplit(url).hostname
    return hostname or ""


def _top(counter: Counter[str], limit: int) -> tuple[NamedCount, ...]:
    """The ``limit`` most frequent names, ties broken alphabetically."""
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return tuple(NamedCount(name, count) for name, count in ranked[:limit])


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    """Distribution as a dict, keys ordered for stable display."""
    return dict(sorted(counter.items()))


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    """Nearest-rank percentile of a list (which may be reordered in place)."""
    if not values:
        return None
    values.sort()
    rank = max(1, math.ceil(percentile / 100 * len(values)))
    return values[rank - 1]
