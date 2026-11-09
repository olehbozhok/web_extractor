"""Single-flight: collapse concurrent identical fetches into one task.

The map ``key -> task`` is guarded by an asyncio lock. Waiters await the
shared task through ``asyncio.shield``, so cancelling one waiter does not
cancel the shared fetch; the task removes itself from the map in ``finally``
so failures and cancellations never leave stale entries. Fresh requests use
a separate key space: a ``fresh=true`` request never joins an in-flight
non-fresh task that started before it (docs/02_architecture.md, section 4),
but fresh requests among themselves still deduplicate.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_FRESH_PREFIX = "fresh:"
"""Prefix separating the fresh-task key space from the shared one."""


def flight_key(url_key: str, *, fresh: bool) -> str:
    """The single-flight key for a URL under the fresh/non-fresh rule."""
    return f"{_FRESH_PREFIX}{url_key}" if fresh else url_key


@dataclass(frozen=True, slots=True)
class _Entry[T]:
    task: "asyncio.Task[T]"


class SingleFlight[T]:
    """Deduplicates concurrent work by key; runners register themselves."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Run ``factory`` under ``key``, or await the already-running task.

        Returns the result and whether this call started the shared task
        (a joiner piggybacks on someone else's work). Callers that must not
        join an older in-flight task (the ``fresh`` rule) pick a different
        key via :func:`flight_key`.
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                task = asyncio.create_task(
                    self._run_and_release(key, factory), name=f"single-flight:{key}"
                )
                self._entries[key] = _Entry(task)
                started_by_self = True
            else:
                task = entry.task
                started_by_self = False
        # shield: cancelling this waiter must not cancel the shared work.
        result = await asyncio.shield(task)
        return result, started_by_self

    async def _run_and_release(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        try:
            return await factory()
        finally:
            async with self._lock:
                self._entries.pop(key, None)
