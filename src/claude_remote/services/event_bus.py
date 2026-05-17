"""In-process async pub/sub that backs Server-Sent Events (mvp-sse).

The hook receiver is synchronous, so ``publish()`` is a non-blocking sync
call. Each subscriber holds an ``asyncio.Queue(maxsize=1)`` used as a
coalescing "dirty" flag: a burst of hook events (Claude fires several in a
row) collapses to a single pending tick, so the SSE loop re-renders once
instead of N times.

Single-process only — this lives in process memory. The systemd unit runs
uvicorn without ``--workers`` so there is exactly one process; if that ever
changes this must move to an external broker (Redis pub/sub).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[None]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self) -> None:
        """Signal "something changed" to every subscriber. Never blocks,
        never raises — a full queue already has a pending tick (coalesced)."""
        for q in self._subscribers:
            with suppress(asyncio.QueueFull):
                q.put_nowait(None)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[None]]:
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)


# Shared process-wide instance: the hook receiver publishes here, SSE
# endpoints subscribe here.
bus = EventBus()
