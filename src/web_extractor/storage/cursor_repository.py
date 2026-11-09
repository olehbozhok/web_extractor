"""Short pagination cursors for the MCP interface (docs/03, section 3.3).

A cursor is a 6-character random-looking identifier over server-side state:
the same ``(url_key, text_sha256, offset, max_chars, format)`` always yields
the same cursor id (deterministic derivation from a SHA-256 of the state),
so repeated calls do not grow the table. The alphabet excludes easily
confused characters (0/o/1/i/l).
"""

import hashlib
import sqlite3
from dataclasses import dataclass

from web_extractor.domain.types import PageFormat
from web_extractor.storage.database import Database
from web_extractor.storage.rows import (
    DataCorruptionError,
    as_int,
    as_str,
    row_value,
)

CURSOR_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
"""Base-31 alphabet without visually ambiguous characters."""

CURSOR_LENGTH = 6
"""Cursor ids are 6 characters; 31^6 ≈ 887M distinct values."""

_MAX_DERIVATION_ATTEMPTS = 8
"""Retries on a derived-id collision with a *different* state; practically
unreachable, but unbounded loops are forbidden."""

_FORMAT_TABLE: dict[str, PageFormat] = {"markdown": "markdown", "text": "text"}
"""Page formats as stored, mapping back onto the Literal type."""


@dataclass(frozen=True, slots=True)
class CursorDraft:
    """The state a cursor is created for, before an id is assigned."""

    url_key: str
    text_sha256: str
    offset: int
    max_chars: int
    page_format: PageFormat
    created_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class CursorState:
    """One row of the ``cursors`` table."""

    id: str
    url_key: str
    text_sha256: str
    offset: int
    max_chars: int
    format: PageFormat
    created_at: str
    expires_at: str


class CursorRepository:
    """Create, resolve, refresh and clean up pagination cursors."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, draft: CursorDraft) -> CursorState:
        """Insert the cursor for this state, or return the existing one.

        Derivation is deterministic, so the same state always maps to the
        same id. A collision with a different state (which would corrupt
        pagination) is retried with a salted derivation.
        """
        state_key = _state_key(
            draft.url_key,
            draft.text_sha256,
            draft.offset,
            draft.max_chars,
            draft.page_format,
        )
        for attempt in range(_MAX_DERIVATION_ATTEMPTS):
            candidate_id = _derive_cursor_id(state_key, salt=attempt)
            query = (
                "INSERT INTO cursors (id, url_key, text_sha256, offset, "
                "max_chars, format, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            )
            params = (
                candidate_id,
                draft.url_key,
                draft.text_sha256,
                draft.offset,
                draft.max_chars,
                draft.page_format,
                draft.created_at,
                draft.expires_at,
            )
            try:
                await self._database.connection.execute(query, params)
            except sqlite3.IntegrityError:
                existing = await self._find_by_state(draft)
                if existing is not None:
                    return existing
                continue  # derived id belongs to a different state; re-derive
            await self._database.connection.commit()
            return CursorState(
                id=candidate_id,
                url_key=draft.url_key,
                text_sha256=draft.text_sha256,
                offset=draft.offset,
                max_chars=draft.max_chars,
                format=draft.page_format,
                created_at=draft.created_at,
                expires_at=draft.expires_at,
            )
        msg = f"could not derive a free cursor id for state {state_key!r}"
        raise RuntimeError(msg)

    async def get(self, cursor_id: str) -> CursorState | None:
        """The cursor with this id, or None (unknown cursor)."""
        async with self._database.connection.execute(
            "SELECT id, url_key, text_sha256, offset, max_chars, format, "
            "created_at, expires_at FROM cursors WHERE id = ?",
            (cursor_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _state_from_row(row)

    async def touch(self, cursor_id: str, expires_at: str) -> bool:
        """Extend the cursor's TTL on use; True when the cursor existed."""
        cursor = await self._database.connection.execute(
            "UPDATE cursors SET expires_at = ? WHERE id = ?",
            (expires_at, cursor_id),
        )
        await self._database.connection.commit()
        return cursor.rowcount > 0

    async def purge_expired(self, now_iso: str) -> int:
        """Delete cursors whose TTL has passed; returns the deleted count."""
        cursor = await self._database.connection.execute(
            "DELETE FROM cursors WHERE expires_at <= ?", (now_iso,)
        )
        await self._database.connection.commit()
        return cursor.rowcount

    async def _find_by_state(self, draft: CursorDraft) -> CursorState | None:
        """Look up an existing cursor by its full state tuple."""
        query = (
            "SELECT id, url_key, text_sha256, offset, max_chars, format, "
            "created_at, expires_at FROM cursors WHERE url_key = ? "
            "AND text_sha256 = ? AND offset = ? AND max_chars = ? AND format = ?"
        )
        params = (
            draft.url_key,
            draft.text_sha256,
            draft.offset,
            draft.max_chars,
            draft.page_format,
        )
        async with self._database.connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _state_from_row(row)


def _state_key(
    url_key: str, text_sha256: str, offset: int, max_chars: int, page_format: str
) -> str:
    """Canonical string form of the cursor state, hashed for derivation."""
    return "\x1f".join((url_key, text_sha256, str(offset), str(max_chars), page_format))


def _derive_cursor_id(state_key: str, salt: int) -> str:
    """Map a cursor state deterministically onto a short id."""
    digest = hashlib.sha256(f"{salt}\x1f{state_key}".encode()).digest()
    return "".join(CURSOR_ALPHABET[byte % len(CURSOR_ALPHABET)] for byte in digest[:CURSOR_LENGTH])


def _state_from_row(row: sqlite3.Row) -> CursorState:
    """Convert one raw row into a CursorState."""
    return CursorState(
        id=as_str(row_value(row, "id"), "id"),
        url_key=as_str(row_value(row, "url_key"), "url_key"),
        text_sha256=as_str(row_value(row, "text_sha256"), "text_sha256"),
        offset=as_int(row_value(row, "offset"), "offset"),
        max_chars=as_int(row_value(row, "max_chars"), "max_chars"),
        format=_page_format(row_value(row, "format")),
        created_at=as_str(row_value(row, "created_at"), "created_at"),
        expires_at=as_str(row_value(row, "expires_at"), "expires_at"),
    )


def _page_format(value: object) -> PageFormat:
    """Narrow a stored format string onto the PageFormat literal."""
    text = as_str(value, "format")
    narrowed = _FORMAT_TABLE.get(text)
    if narrowed is None:
        msg = f"column 'format' must be a known page format, got {text!r}"
        raise DataCorruptionError(msg)
    return narrowed
