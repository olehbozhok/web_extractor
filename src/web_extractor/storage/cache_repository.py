"""Repository for cached page records (``cache_pages`` table)."""

import sqlite3

from web_extractor.domain.records import PageRecord
from web_extractor.domain.types import ErrorKind
from web_extractor.storage.database import Database
from web_extractor.storage.rows import (
    as_fetcher,
    as_int,
    as_opt_bytes,
    as_opt_error_kind,
    as_opt_int,
    as_opt_page_format,
    as_opt_str,
    as_status,
    as_str,
    row_value,
)

_GET_QUERY = (
    "SELECT url_key, final_url, status, http_status, fetcher, fetched_at, "
    "expires_at, content_sha256, html, text, text_format, metadata_json, "
    "error_kind, error_message FROM cache_pages WHERE url_key = ?"
)
"""Column list is a fixed constant, never built from input (S608)."""


class CacheRepository:
    """Read and write cache entries; one entry per normalized URL."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, url_key: str) -> PageRecord | None:
        """The entry cached under ``url_key``, or None."""
        async with self._database.connection.execute(_GET_QUERY, (url_key,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    async def put(self, record: PageRecord) -> None:
        """Insert or replace the entry for ``record.url_key``."""
        query = """
            INSERT INTO cache_pages (
                url_key, final_url, status, http_status, fetcher, fetched_at,
                expires_at, content_sha256, html, text, text_format,
                metadata_json, error_kind, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_key) DO UPDATE SET
                final_url = excluded.final_url,
                status = excluded.status,
                http_status = excluded.http_status,
                fetcher = excluded.fetcher,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at,
                content_sha256 = excluded.content_sha256,
                html = excluded.html,
                text = excluded.text,
                text_format = excluded.text_format,
                metadata_json = excluded.metadata_json,
                error_kind = excluded.error_kind,
                error_message = excluded.error_message
        """
        await self._database.connection.execute(
            query,
            (
                record.url_key,
                record.final_url,
                record.status,
                record.http_status,
                record.fetcher,
                record.fetched_at,
                record.expires_at,
                record.content_sha256,
                record.html,
                record.text,
                record.text_format,
                record.metadata_json,
                record.error_kind,
                record.error_message,
            ),
        )
        await self._database.connection.commit()

    async def list_entries(
        self, *, domain: str | None = None, limit: int, offset: int
    ) -> tuple[PageRecord, ...]:
        """Entries ordered by fetch time descending; domain filters by LIKE."""
        query = (
            "SELECT url_key, final_url, status, http_status, fetcher, fetched_at, "
            "expires_at, content_sha256, html, text, text_format, metadata_json, "
            "error_kind, error_message FROM cache_pages"
        )
        params: list[str] = []
        if domain is not None:
            query += " WHERE url_key LIKE ?"
            params.append(f"%://{domain}/%")
        query += " ORDER BY fetched_at DESC, url_key ASC LIMIT ? OFFSET ?"
        async with self._database.connection.execute(query, (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
        return tuple(_record_from_row(row) for row in rows)

    async def count_entries(self, *, domain: str | None = None) -> int:
        """Number of cache entries, optionally filtered by domain."""
        query = "SELECT COUNT(*) AS total FROM cache_pages"
        params: list[str] = []
        if domain is not None:
            query += " WHERE url_key LIKE ?"
            params.append(f"%://{domain}/%")
        async with self._database.connection.execute(query, tuple(params)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            msg = "COUNT(*) did not yield a row"
            raise RuntimeError(msg)
        return as_int(row_value(row, "total"), "total")

    async def delete(self, url_key: str) -> bool:
        """Remove the entry; True when one existed."""
        cursor = await self._database.connection.execute(
            "DELETE FROM cache_pages WHERE url_key = ?", (url_key,)
        )
        await self._database.connection.commit()
        return cursor.rowcount > 0


def _record_from_row(row: sqlite3.Row) -> PageRecord:
    """Convert one raw row into a validated PageRecord."""
    return PageRecord(
        url_key=as_str(row_value(row, "url_key"), "url_key"),
        final_url=as_str(row_value(row, "final_url"), "final_url"),
        status=as_status(row_value(row, "status"), "status"),
        http_status=as_opt_int(row_value(row, "http_status"), "http_status"),
        fetcher=as_fetcher(row_value(row, "fetcher"), "fetcher"),
        fetched_at=as_str(row_value(row, "fetched_at"), "fetched_at"),
        expires_at=as_str(row_value(row, "expires_at"), "expires_at"),
        content_sha256=as_opt_str(row_value(row, "content_sha256"), "content_sha256"),
        html=as_opt_bytes(row_value(row, "html"), "html"),
        text=as_opt_str(row_value(row, "text"), "text"),
        text_format=as_opt_page_format(row_value(row, "text_format"), "text_format"),
        metadata_json=as_str(row_value(row, "metadata_json"), "metadata_json"),
        error_kind=_opt_error_kind(row),
        error_message=as_opt_str(row_value(row, "error_message"), "error_message"),
    )


def _opt_error_kind(row: sqlite3.Row) -> ErrorKind | None:
    return as_opt_error_kind(row_value(row, "error_kind"), "error_kind")
