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
from web_extractor.storage.stats import collect_period_stats
from web_extractor.web.view import dashboard_view, is_valid_period, period_hours

_WEB_DIR = Path(__file__).parent


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
