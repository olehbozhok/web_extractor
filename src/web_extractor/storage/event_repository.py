"""Repository for the append-only event journal (``events`` table)."""

from web_extractor.domain.records import Event
from web_extractor.storage.database import Database
from web_extractor.storage.rows import as_int, row_value


class EventRepository:
    """Append events; nothing in the journal is ever updated or deleted."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, event: Event) -> None:
        """Write one event; ids are assigned by SQLite."""
        await self._database.connection.execute(
            """
            INSERT INTO events (
                ts, agent, task, mcp_session_id, request_id, tool, url,
                final_url, cache_hit, fresh, fetcher, status, http_status,
                duration_ms, queue_ms, bytes, content_sha256, error_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.ts,
                event.agent,
                event.task,
                event.mcp_session_id,
                event.request_id,
                event.tool,
                event.url,
                event.final_url,
                int(event.cache_hit),
                int(event.fresh),
                event.fetcher,
                event.status,
                event.http_status,
                event.duration_ms,
                event.queue_ms,
                event.size_bytes,
                event.content_sha256,
                event.error_kind,
            ),
        )
        await self._database.connection.commit()

    async def count(self) -> int:
        """Total number of events; used by tests and health checks."""
        async with self._database.connection.execute(
            "SELECT COUNT(*) AS total FROM events"
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            msg = "COUNT(*) did not yield a row"
            raise RuntimeError(msg)
        return as_int(row_value(row, "total"), "total")
