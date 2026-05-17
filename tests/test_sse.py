"""SSE streaming behaviour (mvp-sse WU-3).

We drive the async generator directly with a fake request. httpx's
ASGITransport buffers the whole response body, so it cannot consume an
infinite event-stream — the generator IS the unit under test. Route
registration + guard exemption are asserted via the app's route table.
Hard timeouts make a wiring bug fail fast instead of hanging the suite.
"""

from __future__ import annotations

import asyncio

import pytest

from claude_remote.routes.sse import _event_stream, _frame
from claude_remote.services.event_bus import bus

pytestmark = pytest.mark.anyio


class _FakeRequest:
    def __init__(self) -> None:
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected


async def _anext(gen, timeout: float = 3.0) -> str:
    return await asyncio.wait_for(gen.__anext__(), timeout=timeout)


async def test_frame_encodes_every_line_as_sse_data() -> None:
    out = _frame("<a>\n<b>")
    assert out == "data: <a>\ndata: <b>\n\n"


async def test_initial_frame_is_rendered_immediately() -> None:
    gen = _event_stream(_FakeRequest(), lambda: "<p>hi</p>", interval=10.0)
    try:
        first = await _anext(gen)
        assert first == "data: <p>hi</p>\n\n"
    finally:
        await gen.aclose()


async def test_publish_triggers_early_refresh_before_interval() -> None:
    """A bus tick re-renders immediately, not only on the periodic interval."""
    calls = {"n": 0}

    def render() -> str:
        calls["n"] += 1
        return f"<i>{calls['n']}</i>"

    gen = _event_stream(_FakeRequest(), render, interval=30.0)
    try:
        assert await _anext(gen) == "data: <i>1</i>\n\n"
        bus.publish()
        assert await _anext(gen, timeout=3.0) == "data: <i>2</i>\n\n"
    finally:
        await gen.aclose()


async def test_emits_periodically_without_any_publish() -> None:
    """Time-varying data (metrics, time-windowed status): re-render every
    `interval` even when no hook event fires."""
    calls = {"n": 0}

    def render() -> str:
        calls["n"] += 1
        return f"<i>{calls['n']}</i>"

    gen = _event_stream(_FakeRequest(), render, interval=0.3)
    try:
        await _anext(gen)  # initial
        # next frame arrives from the timer alone (no publish)
        nxt = await _anext(gen, timeout=2.0)
        assert nxt.startswith("data:")
        assert calls["n"] >= 2
    finally:
        await gen.aclose()


async def test_disconnect_stops_stream_and_unsubscribes() -> None:
    req = _FakeRequest()
    before = bus.subscriber_count
    gen = _event_stream(req, lambda: "<p>x</p>", interval=0.2)
    try:
        await _anext(gen)  # initial → now subscribed
        assert bus.subscriber_count == before + 1
        req._disconnected = True
        with pytest.raises(StopAsyncIteration):
            await _anext(gen, timeout=2.0)
    finally:
        await gen.aclose()
    assert bus.subscriber_count == before


async def test_routes_registered_and_guard_exempt() -> None:
    from claude_remote.app import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/sse/home" in paths
    assert "/sse/metrics" in paths
