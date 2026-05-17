"""In-process async pub/sub bus that backs SSE (mvp-sse WU-1).

Contract: publish() is a non-blocking SYNC call (the hook receiver is sync);
each subscriber gets a coalescing "dirty" signal (at most one pending tick,
so a burst of hook events collapses to a single re-render); unsubscribing
must not leak the queue.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest


async def test_publish_delivers_tick_to_subscriber() -> None:
    from claude_remote.services.event_bus import EventBus

    bus = EventBus()
    async with bus.subscribe() as q:
        bus.publish()
        await asyncio.wait_for(q.get(), timeout=1)


async def test_multiple_subscribers_all_receive() -> None:
    from claude_remote.services.event_bus import EventBus

    bus = EventBus()
    async with bus.subscribe() as a, bus.subscribe() as b:
        assert bus.subscriber_count == 2
        bus.publish()
        await asyncio.wait_for(a.get(), timeout=1)
        await asyncio.wait_for(b.get(), timeout=1)


async def test_unsubscribe_removes_and_no_leak() -> None:
    from claude_remote.services.event_bus import EventBus

    bus = EventBus()
    async with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
    bus.publish()  # no subscribers — must not raise


def test_publish_with_no_subscribers_is_noop() -> None:
    from claude_remote.services.event_bus import EventBus

    EventBus().publish()


async def test_publish_coalesces_when_pending() -> None:
    from claude_remote.services.event_bus import EventBus

    bus = EventBus()
    async with bus.subscribe() as q:
        bus.publish()
        bus.publish()
        bus.publish()
        await asyncio.wait_for(q.get(), timeout=1)
        with pytest.raises(asyncio.QueueEmpty):
            q.get_nowait()


async def test_subscribe_is_reentrant_safe_under_concurrent_publish() -> None:
    from claude_remote.services.event_bus import EventBus

    bus = EventBus()
    async with bus.subscribe() as q:

        async def spam() -> None:
            for _ in range(50):
                bus.publish()
                await asyncio.sleep(0)

        task = asyncio.create_task(spam())
        await asyncio.wait_for(q.get(), timeout=1)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
