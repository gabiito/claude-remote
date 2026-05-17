"""Presence-aware push (#6) — wiring guards only.

The real behaviour (a focused, recently-active window suppresses the
phone buzz) is Service-Worker + browser JS and must be verified on
device. These tests only guard that the pieces are shipped and wired so
a refactor can't silently drop them.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_presence_js_is_served_and_pings_sw(
    async_client_with_db: AsyncClient,
) -> None:
    js = (await async_client_with_db.get("/static/js/presence.js")).text
    assert "cr-activity" in js
    assert "serviceWorker" in js
    assert "visibilitychange" in js


async def test_base_loads_presence_js(async_client_with_db: AsyncClient) -> None:
    html = (await async_client_with_db.get("/")).text
    assert "js/presence.js" in html


async def test_sw_suppresses_when_focused_and_recently_active(
    async_client_with_db: AsyncClient,
) -> None:
    sw = (await async_client_with_db.get("/sw.js")).text
    # message handler records activity
    assert "cr-activity" in sw
    assert "addEventListener('message'" in sw
    # push handler gates on focus/visibility + a recent-activity window
    assert "matchAll" in sw
    assert "ACTIVITY_WINDOW_MS" in sw
    # SW must re-install (version bumped off the original 'v1')
    assert "SW_VERSION = 'v1'" not in sw
