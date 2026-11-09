"""Migration tests: fresh, repeated, and already-migrated databases."""

from pathlib import Path

import pytest
import pytest_asyncio

from web_extractor.storage.database import Database

_TABLES_EXPECTED = {"cache_pages", "cursors", "events", "schema_version"}

_LATEST_VERSION = 3
"""The newest bundled migration; bump when adding 003_...sql."""


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "migrations.db")


async def _table_names(db: Database) -> set[str]:
    async with db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ) as cursor:
        rows = await cursor.fetchall()
    return {row["name"] for row in rows}


async def _current_version(db: Database) -> int:
    async with db.connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return int(row["version"])


@pytest.mark.asyncio
async def test_fresh_database_gets_all_tables(db_path: str) -> None:
    async with Database(db_path) as db:
        assert await _table_names(db) >= _TABLES_EXPECTED
        assert await _current_version(db) == _LATEST_VERSION


@pytest.mark.asyncio
async def test_reopening_is_idempotent(db_path: str) -> None:
    async with Database(db_path) as db:
        first_tables = await _table_names(db)
    async with Database(db_path) as db:
        assert await _table_names(db) == first_tables
        assert await _current_version(db) == _LATEST_VERSION


@pytest.mark.asyncio
async def test_foreign_keys_and_wal_are_enabled(db_path: str) -> None:
    async with Database(db_path) as db:
        async with db.connection.execute("PRAGMA journal_mode") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row["journal_mode"] == "wal"
        async with db.connection.execute("PRAGMA foreign_keys") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row["foreign_keys"] == 1
