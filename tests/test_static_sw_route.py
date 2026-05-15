"""RED tests for WU-4 — GET /sw.js (static service worker route).

Tests run BEFORE the implementation exists; they must all fail.
Once the green commit lands, all tests here must pass.

Spec: REQ-8 (SC-8.1–8.4)
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_sw_js_returns_200(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js returns HTTP 200. (SC-8.1)"""
    response = await async_client_with_db.get("/sw.js")
    assert response.status_code == 200


async def test_sw_js_has_service_worker_allowed_header(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js includes Service-Worker-Allowed: / header. (ADR-10)"""
    response = await async_client_with_db.get("/sw.js")
    assert response.headers.get("service-worker-allowed") == "/"


async def test_sw_js_has_no_cache_header(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js includes Cache-Control: no-cache header. (ADR-10)"""
    response = await async_client_with_db.get("/sw.js")
    assert "no-cache" in response.headers.get("cache-control", "")


async def test_sw_js_content_contains_sw_version(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js response body contains SW_VERSION = 'v1'. (SC-8.2, REQ-8.2)"""
    response = await async_client_with_db.get("/sw.js")
    assert "SW_VERSION" in response.text
    assert "v1" in response.text


async def test_sw_js_contains_push_event_listener(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js body contains push event listener and showNotification. (SC-8.3)"""
    response = await async_client_with_db.get("/sw.js")
    assert "addEventListener" in response.text
    assert "push" in response.text
    assert "showNotification" in response.text


async def test_sw_js_contains_notificationclick_listener(async_client_with_db: AsyncClient) -> None:
    """GET /sw.js body contains notificationclick listener and openWindow. (SC-8.4)"""
    response = await async_client_with_db.get("/sw.js")
    assert "notificationclick" in response.text
    assert "openWindow" in response.text
