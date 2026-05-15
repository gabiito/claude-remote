"""Tests for the custom 404 handler and page structure — WU-7 (red/green).

Covers:
  - GET /nonexistent-route → 404 status + HTML body containing "No encontrado"
  - HTML body contains href="/" back-to-home link
  - HTML body does NOT contain FastAPI default plain-text "Not Found"
  - GET /openapi.json → 200 JSON (handler does not intercept valid API routes)
  - GET /docs → accessible, not intercepted by custom 404
  - GET /static/css/app.css → 200 (static assets still served)
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_unknown_route_returns_404(async_client: AsyncClient) -> None:
    """GET /does-not-exist → 404 status code."""
    response = await async_client.get("/does-not-exist")
    assert response.status_code == 404


async def test_custom_404_contains_no_encontrado(async_client: AsyncClient) -> None:
    """GET /nonexistent → 404 HTML body contains 'No encontrado'."""
    response = await async_client.get("/nonexistent-route-xyz")
    assert response.status_code == 404
    html = response.text
    assert "No encontrado" in html


async def test_custom_404_contains_back_link(async_client: AsyncClient) -> None:
    """GET /nonexistent → HTML body contains href="/" back-to-home link."""
    response = await async_client.get("/some-missing-page")
    assert response.status_code == 404
    html = response.text
    assert 'href="/"' in html


async def test_custom_404_does_not_contain_default_not_found(async_client: AsyncClient) -> None:
    """GET /nonexistent → HTML body does NOT contain FastAPI plain-text 'Not Found'."""
    response = await async_client.get("/another-missing-page")
    assert response.status_code == 404
    html = response.text
    # FastAPI default: {"detail": "Not Found"}
    assert '"Not Found"' not in html


async def test_openapi_json_not_intercepted(async_client: AsyncClient) -> None:
    """GET /openapi.json → 200 JSON response (custom 404 handler must not intercept)."""
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")


async def test_docs_not_intercepted(async_client: AsyncClient) -> None:
    """GET /docs → not intercepted by the custom 404 handler (docs remain accessible)."""
    response = await async_client.get("/docs")
    # FastAPI docs returns 200
    assert response.status_code == 200


async def test_static_css_still_served(async_client: AsyncClient) -> None:
    """GET /static/css/app.css → 200 (static assets not broken by 404 handler)."""
    response = await async_client.get("/static/css/app.css")
    assert response.status_code == 200
