"""Per-host and global concurrency limits for outgoing fetches.

For every host: a semaphore caps simultaneous requests and a minimum
interval separates consecutive starts (politeness). One global semaphore
caps the total in-flight fetches. Time spent waiting for the limiter is
measured and reported separately as ``queue_ms``; it never counts into
network time.

The wait between requests is performed through an injected ``Sleeper`` so
tests can assert the computed waits without sleeping.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


class Sleeper(Protocol):
    """Suspends the coroutine; the real implementation wraps asyncio.sleep."""

    async def sleep(self, seconds: float) -> None:
        """Suspend for ``seconds``; implementations may fake this."""
        ...  # pragma: no cover


class RealSleeper:
    """Sleeps through ``asyncio.sleep``; zero-second waits do not yield."""

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


@dataclass(frozen=True, slots=True)
class HostLimit:
    """Per-host limits; overrides replace the defaults per hostname."""

    max_concurrent: int
    min_interval_s: float


@dataclass(frozen=True, slots=True)
class LimiterLease:
    """A held limiter slot; ``queue_ms`` is how long acquiring took."""

    queue_ms: int
    _semaphore: asyncio.Semaphore
    _global: asyncio.Semaphore

    def release(self) -> None:
        """Return both slots; must be called exactly once, in ``finally``."""
        self._semaphore.release()
        self._global.release()


@dataclass(slots=True)
class _HostState:
    semaphore: asyncio.Semaphore
    min_interval_s: float
    last_start_monotonic: float | None = None


@dataclass
class DomainLimiter:
    """Hands out per-host and global fetch slots."""

    default_limit: HostLimit
    overrides: Mapping[str, HostLimit] = field(default_factory=dict)
    global_max_concurrent: int = 20
    sleeper: Sleeper = field(default_factory=RealSleeper)

    def __post_init__(self) -> None:
        self._global = asyncio.Semaphore(self.global_max_concurrent)
        self._hosts: dict[str, _HostState] = {}

    async def acquire(self, host: str) -> LimiterLease:
        """Wait for the global slot and the host's slot plus its interval.

        Order matters: the global slot is taken first, so a slow host
        cannot monopolize all in-flight requests while others are capped
        out globally anyway.
        """
        started = time.monotonic()
        await self._global.acquire()
        state = self._host_state(host)
        await state.semaphore.acquire()
        wait = self._interval_wait(state)
        if wait > 0:
            await self.sleeper.sleep(wait)
        state.last_start_monotonic = time.monotonic()
        queue_ms = int((time.monotonic() - started) * 1000)
        return LimiterLease(queue_ms=queue_ms, _semaphore=state.semaphore, _global=self._global)

    def _host_state(self, host: str) -> _HostState:
        limit = self.overrides.get(host, self.default_limit)
        state = self._hosts.get(host)
        if state is None or state.min_interval_s != limit.min_interval_s:
            state = _HostState(
                semaphore=asyncio.Semaphore(limit.max_concurrent),
                min_interval_s=limit.min_interval_s,
            )
            self._hosts[host] = state
        return state

    def _interval_wait(self, state: _HostState) -> float:
        """Seconds the next request must wait since the previous start."""
        if state.last_start_monotonic is None:
            return 0.0
        elapsed = time.monotonic() - state.last_start_monotonic
        return max(0.0, state.min_interval_s - elapsed)
