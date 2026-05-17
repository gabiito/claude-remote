"""Frontend is wired to SSE, not 5s polling (mvp-sse WU-4).

Browser behaviour (live swap, expand-guard, filter reconnect) needs
on-device verification — these only guard the wiring/regression surface:
the SSE client is shipped and the old poll triggers are gone.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_sse_client_script_is_served(async_client_with_db: AsyncClient) -> None:
    resp = await async_client_with_db.get("/static/js/sse.js")
    assert resp.status_code == 200
    assert "EventSource" in resp.text


async def test_home_loads_sse_client_and_drops_poll(
    async_client_with_db: AsyncClient,
) -> None:
    html = (await async_client_with_db.get("/")).text
    assert "js/sse.js" in html
    # the 5s whole-list poll trigger must be gone
    assert "/ui/home/list" not in html
    assert "every 5s" not in html


async def test_metrics_body_drops_poll(async_client_with_db: AsyncClient) -> None:
    html = (await async_client_with_db.get("/metrics")).text
    assert 'id="cr-metrics-body"' in html
    # body no longer self-polls every 5s
    assert "every 5s" not in html
