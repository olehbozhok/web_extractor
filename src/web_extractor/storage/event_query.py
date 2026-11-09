"""Filtered, sorted, paginated event queries for the Events and Agents pages.

Filter values travel as SQL parameters; the sort column is chosen from a
fixed whitelist so nothing user-supplied ever reaches SQL text
(AGENTS.md section 9).
"""

import sqlite3
from dataclasses import dataclass
from typing import Literal

from web_extractor.domain.records import Event
from web_extractor.storage.database import Database
from web_extractor.storage.rows import (
    as_bool,
    as_int,
    as_opt_error_kind,
    as_opt_fetcher,
    as_opt_int,
    as_opt_str,
    as_status,
    as_str,
    as_tool,
    row_value,
)

SortColumn = Literal["ts", "agent", "status", "http_status", "duration_ms"]

_SORT_COLUMNS: dict[str, str] = {
    "ts": "ts",
    "agent": "agent",
    "status": "status",
    "http_status": "http_status",
    "duration_ms": "duration_ms",
}
"""Whitelisted ORDER BY columns mapped to their SQL names."""


@dataclass(frozen=True, slots=True)
class EventFilter:
    """Events page filters; None means "no filter on this field"."""

    since_iso: str
    until_iso: str
    agent: str | None = None
    task: str | None = None
    domain: str | None = None
    status: str | None = None
    cache_hit: bool | None = None
    fetcher: str | None = None


@dataclass(frozen=True, slots=True)
class EventQuery:
    """A full events-page query: filters plus ordering and pagination."""

    event_filter: EventFilter
    order_by: SortColumn = "ts"
    descending: bool = True
    page: int = 1
    page_size: int = 25


@dataclass(frozen=True, slots=True)
class EventPage:
    """One page of the events table plus the total for pagination."""

    events: tuple[Event, ...]
    total: int


@dataclass(frozen=True, slots=True)
class AgentSummary:
    """Per-agent aggregates for the Agents page."""

    agent: str
    total: int
    ok_count: int
    error_count: int
    cache_hits: int
    distinct_urls: int
    top_urls: tuple[str, ...]


async def list_events(database: Database, query: EventQuery) -> EventPage:
    """One page of filtered events plus the total matching count."""
    clauses, params = _filter_clauses(query.event_filter)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    column = _SORT_COLUMNS[query.order_by]
    direction = "DESC" if query.descending else "ASC"
    base_query = f"FROM events {where}"
    count_query = f"SELECT COUNT(*) AS total {base_query}"
    rows_query = (
        f"SELECT ts, agent, task, mcp_session_id, request_id, tool, url, final_url, "
        f"cache_hit, fresh, fetcher, status, http_status, duration_ms, queue_ms, "
        f"bytes, content_sha256, error_kind {base_query} "
        f"ORDER BY {column} {direction}, id ASC LIMIT ? OFFSET ?"
    )
    connection = database.connection
    async with connection.execute(count_query, tuple(params)) as cursor:
        count_row = await cursor.fetchone()
    if count_row is None:
        msg = "COUNT(*) did not yield a row"
        raise RuntimeError(msg)
    total = as_int(row_value(count_row, "total"), "total")
    offset = (query.page - 1) * query.page_size
    # LIMIT comes first, OFFSET second.
    async with connection.execute(rows_query, (*params, query.page_size, offset)) as cursor:
        raw_rows = await cursor.fetchall()
    return EventPage(
        events=tuple(_event_from_row(row) for row in raw_rows),
        total=total,
    )


async def agent_summaries(
    database: Database,
    *,
    since_iso: str,
    until_iso: str,
) -> tuple[AgentSummary, ...]:
    """Per-agent request aggregates for the Agents page, busiest first."""
    query = (
        "SELECT agent, COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count, "
        "SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS error_count, "
        "SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_hits, "
        "COUNT(DISTINCT url) AS distinct_urls "
        "FROM events WHERE ts >= ? AND ts <= ? GROUP BY agent ORDER BY total DESC"
    )
    url_query = (
        "SELECT agent, url, COUNT(*) AS total FROM events "
        "WHERE ts >= ? AND ts <= ? GROUP BY agent, url"
    )
    connection = database.connection
    async with connection.execute(query, (since_iso, until_iso)) as cursor:
        rows = await cursor.fetchall()
    async with connection.execute(url_query, (since_iso, until_iso)) as cursor:
        url_rows = await cursor.fetchall()
    urls_by_agent: dict[str, list[tuple[str, int]]] = {}
    for url_row in url_rows:
        agent = as_str(row_value(url_row, "agent"), "agent")
        url = as_str(row_value(url_row, "url"), "url")
        count = as_int(row_value(url_row, "total"), "total")
        urls_by_agent.setdefault(agent, []).append((url, count))
    summaries: list[AgentSummary] = []
    for row in rows:
        agent = as_str(row_value(row, "agent"), "agent")
        url_counts = sorted(urls_by_agent.get(agent, []), key=lambda item: (-item[1], item[0]))
        summaries.append(
            AgentSummary(
                agent=agent,
                total=as_int(row_value(row, "total"), "total"),
                ok_count=as_int(row_value(row, "ok_count"), "ok_count"),
                error_count=as_int(row_value(row, "error_count"), "error_count"),
                cache_hits=as_int(row_value(row, "cache_hits"), "cache_hits"),
                distinct_urls=as_int(row_value(row, "distinct_urls"), "distinct_urls"),
                top_urls=tuple(url for url, _count in url_counts[:3]),
            )
        )
    return tuple(summaries)


def _filter_clauses(event_filter: EventFilter) -> tuple[list[str], list[str]]:
    """Build WHERE clauses and their parameter values from the filter."""
    clauses = ["ts >= ?", "ts <= ?"]
    params = [event_filter.since_iso, event_filter.until_iso]
    if event_filter.agent is not None:
        clauses.append("agent = ?")
        params.append(event_filter.agent)
    if event_filter.task is not None:
        clauses.append("task = ?")
        params.append(event_filter.task)
    if event_filter.domain is not None:
        clauses.append("url LIKE ?")
        params.append(f"%://{event_filter.domain}/%")
    if event_filter.status is not None:
        clauses.append("status = ?")
        params.append(event_filter.status)
    if event_filter.cache_hit is not None:
        clauses.append("cache_hit = ?")
        params.append("1" if event_filter.cache_hit else "0")
    if event_filter.fetcher is not None:
        clauses.append("fetcher = ?")
        params.append(event_filter.fetcher)
    return clauses, params


def _event_from_row(row: sqlite3.Row) -> Event:
    """Convert one raw events row into a validated Event."""
    return Event(
        ts=as_str(row_value(row, "ts"), "ts"),
        agent=as_str(row_value(row, "agent"), "agent"),
        task=as_opt_str(row_value(row, "task"), "task"),
        mcp_session_id=as_opt_str(row_value(row, "mcp_session_id"), "mcp_session_id"),
        request_id=as_str(row_value(row, "request_id"), "request_id"),
        tool=as_tool(row_value(row, "tool"), "tool"),
        url=as_str(row_value(row, "url"), "url"),
        final_url=as_opt_str(row_value(row, "final_url"), "final_url"),
        cache_hit=as_bool(row_value(row, "cache_hit"), "cache_hit"),
        fresh=as_bool(row_value(row, "fresh"), "fresh"),
        fetcher=as_opt_fetcher(row_value(row, "fetcher"), "fetcher"),
        status=as_status(row_value(row, "status"), "status"),
        http_status=as_opt_int(row_value(row, "http_status"), "http_status"),
        duration_ms=as_int(row_value(row, "duration_ms"), "duration_ms"),
        queue_ms=as_int(row_value(row, "queue_ms"), "queue_ms"),
        size_bytes=as_opt_int(row_value(row, "bytes"), "bytes"),
        content_sha256=as_opt_str(row_value(row, "content_sha256"), "content_sha256"),
        error_kind=as_opt_error_kind(row_value(row, "error_kind"), "error_kind"),
    )
