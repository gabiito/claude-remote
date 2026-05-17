"""SSE endpoints stream re-rendered partials on bus ticks (mvp-sse WU-3).

These replace the 5s HTMX polling of the home list and the metrics body.
Tests use real streaming with hard timeouts so a wiring bug fails fast
instead of hanging the suite.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def _read_frame(aiter, timeout: float = 3.0) -> str:
    """Read one streamed chunk (an SSE frame) or fail fast."""
    return await asyncio.wait_for(aiter.__anext__(), timeout=timeout)


async def test_sse_home_is_event_stream_with_initial_frame(
    async_client_with_db: AsyncClient,
) -> None:
    async with async_client_with_db.stream("GET", "/sse/home") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunk = await _read_frame(resp.aiter_text())
        assert chunk.startswith("data:") or chunk.startswith(":")


async def test_sse_home_re_renders_on_publish(
    async_client_with_db: AsyncClient,
) -> None:
    from claude_remote.services.event_bus import bus

    async with async_client_with_db.stream("GET", "/sse/home") as resp:
        ait = resp.aiter_text()
        await _read_frame(ait)  # initial
        bus.publish()
        nxt = await _read_frame(ait)
        assert nxt.startswith("data:")


async def test_sse_metrics_is_event_stream_with_initial_frame(
    async_client_with_db: AsyncClient,
) -> None:
    async with async_client_with_db.stream("GET", "/sse/metrics") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunk = await _read_frame(resp.aiter_text())
        assert chunk.startswith("data:") or chunk.startswith(":")


async def test_sse_unsubscribes_on_client_disconnect(
    async_client_with_db: AsyncClient,
) -> None:
    """Closing the connection must drop the subscriber (no queue leak)."""
    from claude_remote.services.event_bus import bus

    async with async_client_with_db.stream("GET", "/sse/home") as resp:
        await _read_frame(resp.aiter_text())
        assert bus.subscriber_count >= 1

    # Cleanup is driven by generator cancellation — give it a moment.
    for _ in range(50):
        if bus.subscriber_count == 0:
            break
        await asyncio.sleep(0.02)
    assert bus.subscriber_count == 0
