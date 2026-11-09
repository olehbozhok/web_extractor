"""Shared fixtures for storage tests: one migrated database per test."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from web_extractor.storage.database import Database


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    """A connected, migrated database backed by a temporary file."""
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    yield db
    await db.close()
