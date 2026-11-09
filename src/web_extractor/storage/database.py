"""SQLite database access: connection lifecycle, pragmas and migrations.

One ``Database`` instance owns one aiosqlite connection; repositories take
it through the constructor. The connection is opened explicitly with
:meth:`Database.connect` (composition root's job) and every query inside is
parameterized (AGENTS.md section 9).
"""

import importlib.resources
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import aiosqlite

from web_extractor.storage.rows import as_int, row_value

if TYPE_CHECKING:
    from collections.abc import Sequence

_MIGRATIONS_PACKAGE = "web_extractor.storage.migrations"

_MIGRATION_FILE_RE = re.compile(r"(\d{3})_[a-z0-9_]+\.sql\Z")
"""Numbered migration files: ``001_init.sql``, ascending, zero-padded."""

_BUSY_TIMEOUT_MS = 5000
"""How long a writer waits for a competing writer before failing."""


class MigrationError(RuntimeError):
    """Migration files are inconsistent with the applied database state."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class Database:
    """Owns the SQLite connection; applies migrations at startup."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the connection, enable WAL, and apply pending migrations.

        Safe to call on an empty, a fresh, or an already-migrated database;
        applied migrations are never re-run (AGENTS.md section 9).
        """
        if self._connection is not None:
            msg = "Database.connect called twice"
            raise RuntimeError(msg)
        connection = await aiosqlite.connect(self._path)
        connection.row_factory = aiosqlite.Row
        await connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA synchronous = NORMAL")
        try:
            await _apply_migrations(connection)
        except BaseException:
            await connection.close()
            raise
        self._connection = connection

    async def close(self) -> None:
        """Close the connection; idempotent."""
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """The open connection; repositories run their queries on it."""
        if self._connection is None:
            msg = "Database is not connected; call connect() first"
            raise RuntimeError(msg)
        return self._connection

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


async def _apply_migrations(connection: aiosqlite.Connection) -> None:
    """Bring the schema up to the latest migration, in order.

    The version table is bootstrapped outside migrations so that migration
    001 does not depend on the runner's own bookkeeping.
    """
    await connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    async with connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        msg = "schema_version table did not yield a version row"
        raise MigrationError(msg)
    current_version = as_int(row_value(row, "version"), "schema_version.version")
    await _bootstrap_version_row(connection)

    migrations = _discover_migrations()
    for number, sql in migrations:
        if number <= current_version:
            continue
        if number != current_version + 1:
            msg = f"migration gap: current version {current_version}, next file is {number}"
            raise MigrationError(msg)
        await connection.executescript(sql)
        await connection.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        await connection.commit()
        current_version = number


def _discover_migrations() -> "Sequence[tuple[int, str]]":
    """Load bundled migration files in ascending order."""
    package = importlib.resources.files(_MIGRATIONS_PACKAGE)
    entries: list[tuple[int, str]] = []
    for entry in package.iterdir():
        match = _MIGRATION_FILE_RE.fullmatch(PurePosixPath(entry.name).name)
        if match is None:
            continue
        sql = entry.read_text(encoding="utf-8")
        entries.append((int(match.group(1)), sql))
    if not entries:
        msg = "no migration files found in package"
        raise MigrationError(msg)
    entries.sort(key=lambda item: item[0])
    return entries


async def _bootstrap_version_row(connection: aiosqlite.Connection) -> None:
    """Insert the zero version row on a never-populated table.

    Otherwise ``MAX(version)`` would be NULL on the next open and
    versioning would break.
    """
    query = (
        "INSERT INTO schema_version (version) SELECT 0 "
        "WHERE NOT EXISTS (SELECT 1 FROM schema_version)"
    )
    await connection.execute(query)
    await connection.commit()
