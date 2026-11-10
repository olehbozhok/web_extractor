"""Pure view-model helpers for the web pages.

Templates receive numbers computed here, so dashboard math stays testable
without HTTP (AGENTS.md section 7: separate pure logic from I/O).
"""

from web_extractor.storage.stats import PeriodStats

_DASHBOARD_PERIODS: tuple[str, ...] = ("24h", "7d", "30d")
"""Period selector values shown on the dashboard."""

_PERIOD_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def is_valid_period(period: str) -> bool:
    """True when the query parameter names a known dashboard period."""
    return period in _PERIOD_HOURS


def period_hours(period: str) -> int:
    """Hours covered by a period name; callers validate first."""
    return _PERIOD_HOURS[period]


def dashboard_view(stats: PeriodStats) -> dict[str, object]:
    """Derived numbers for the dashboard template (percentages, top lists)."""
    total = stats.total_events
    return {
        "total_events": total,
        "cache_hits": stats.cache_hits,
        "hit_rate_pct": _share(stats.cache_hits, total),
        "by_status": stats.by_status,
        "by_http_status": stats.by_http_status,
        "needs_browser_share_pct": _share(stats.needs_browser_count, total),
        "empty_render_share_pct": _share(stats.empty_render_count, total),
        "unknown_agent_share_pct": _share(stats.unknown_agent_count, total),
        "top_agents": stats.top_agents,
        "top_tasks": stats.top_tasks,
        "top_domains": stats.top_domains,
        "top_urls": stats.top_urls,
        "duration_p50_ms": stats.duration_p50_ms,
        "duration_p95_ms": stats.duration_p95_ms,
    }


def _share(part: int, total: int) -> float:
    """Percentage 0-100, rounded to one decimal; 0.0 for an empty period."""
    if total == 0:
        return 0.0
    return round(part / total * 100, 1)
