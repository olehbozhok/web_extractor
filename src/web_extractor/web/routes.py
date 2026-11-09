"""Web UI routes: server-rendered pages over the storage layer.

The UI never mutates data except the cache page's reload/delete buttons
(stage 2 scope, docs/01_requirements.md section 4.7).
"""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from web_extractor.domain.ttl import to_iso_utc
from web_extractor.domain.types import Clock
from web_extractor.storage.database import Database
from web_extractor.storage.event_query import (
    EventFilter,
    EventQuery,
    SortColumn,
    agent_summaries,
    list_events,
)
from web_extractor.storage.stats import collect_period_stats
from web_extractor.web.view import dashboard_view, is_valid_period, period_hours

_WEB_DIR = Path(__file__).parent


def sort_name(column: str) -> SortColumn:
    """The whitelist-checked sort column; raises HTTP 400 for unknown names."""
    narrowed = _SORTABLE_COLUMNS_BY_NAME.get(column)
    if narrowed is None:
        raise HTTPException(status_code=400, detail=f"unknown sort column {column!r}")
    return narrowed


_PAGE_SIZE = 25

_SORTABLE_COLUMNS_BY_NAME: dict[str, SortColumn] = {
    "ts": "ts",
    "agent": "agent",
    "status": "status",
    "http_status": "http_status",
    "duration_ms": "duration_ms",
}
"""Whitelisted sort columns, mapping the query parameter to its SQL value."""

_SORTABLE_COLUMNS: tuple[str, ...] = tuple(_SORTABLE_COLUMNS_BY_NAME)

_STATUSES: tuple[str, ...] = ("ok", "blocked", "needs_browser", "not_found", "timeout", "error")


def _templates() -> Jinja2Templates:
    """Jinja2 environment with autoescaping always on (AGENTS.md section 10)."""
    environment = Environment(
        loader=FileSystemLoader(str(_WEB_DIR / "templates")),
        autoescape=select_autoescape(enabled_extensions=("html", "jinja", "j2")),
    )
    return Jinja2Templates(env=environment)


def register_web_routes(router: APIRouter, database: Database, clock: Clock) -> None:
    """Register the dashboard; more pages arrive in later milestones."""
    templates = _templates()

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, period: str = "24h") -> HTMLResponse:
        """Statistics for a selectable period (S7)."""
        if not is_valid_period(period):
            raise HTTPException(status_code=400, detail=f"unknown period {period!r}")
        now = clock.now()
        stats = await collect_period_stats(
            database,
            since_iso=to_iso_utc(now - timedelta(hours=period_hours(period))),
            until_iso=to_iso_utc(now),
        )
        rendered = templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "period": period,
                "view": dashboard_view(stats),
            },
        )
        return HTMLResponse(content=rendered.body, status_code=rendered.status_code)

    @router.get("/events", response_class=HTMLResponse)
    async def events(
        request: Request,
        period: str = "24h",
        agent: str = "",
        domain: str = "",
        status: str = "",
        cache_hit: str = "",
        fetcher: str = "",
        sort: str = "ts",
        descending: str = "true",
        page: int = 1,
    ) -> HTMLResponse:
        """The events table with filters, sorting and pagination (S7)."""
        if not is_valid_period(period):
            raise HTTPException(status_code=400, detail=f"unknown period {period!r}")
        if sort not in _SORTABLE_COLUMNS or descending not in ("true", "false"):
            raise HTTPException(status_code=400, detail="bad sort parameters")
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be >= 1")
        now = clock.now()
        since = to_iso_utc(now - timedelta(hours=period_hours(period)))
        until = to_iso_utc(now)
        query = EventQuery(
            event_filter=EventFilter(
                since_iso=since,
                until_iso=until,
                agent=agent or None,
                domain=domain or None,
                status=status or None,
                cache_hit={"true": True, "false": False, "": None}.get(cache_hit),
                fetcher=fetcher or None,
            ),
            order_by=sort_name(sort),
            descending=descending == "true",
            page=page,
            page_size=_PAGE_SIZE,
        )
        event_page = await list_events(database, query)
        rendered = templates.TemplateResponse(
            request=request,
            name="events_table.html" if request.headers.get("HX-Request") else "events.html",
            context={
                "period": period,
                "filters": {
                    "agent": agent,
                    "domain": domain,
                    "status": status,
                    "cache_hit": cache_hit,
                    "fetcher": fetcher,
                },
                "sort": sort,
                "descending": descending == "true",
                "page": page,
                "page_size": _PAGE_SIZE,
                "total": event_page.total,
                "events": event_page.events,
                "statuses": _STATUSES,
                "sortable": _SORTABLE_COLUMNS,
            },
        )
        return HTMLResponse(content=rendered.body, status_code=rendered.status_code)

    @router.get("/agents", response_class=HTMLResponse)
    async def agents(request: Request, period: str = "24h") -> HTMLResponse:
        """Per-agent and per-task breakdown for a period (S7)."""
        if not is_valid_period(period):
            raise HTTPException(status_code=400, detail=f"unknown period {period!r}")
        now = clock.now()
        since = to_iso_utc(now - timedelta(hours=period_hours(period)))
        until = to_iso_utc(now)
        stats = await collect_period_stats(database, since_iso=since, until_iso=until)
        summaries = await agent_summaries(database, since_iso=since, until_iso=until)
        rendered = templates.TemplateResponse(
            request=request,
            name="agents.html",
            context={
                "period": period,
                "summaries": summaries,
                "top_tasks": stats.top_tasks,
            },
        )
        return HTMLResponse(content=rendered.body, status_code=rendered.status_code)
