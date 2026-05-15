"""Tests for WU-6 — push-subscribe.js static asset + base.html integration.

Covers (REQ-9, REQ-12.6):
  - /static/js/push-subscribe.js is served with 200 + JS content-type
  - base.html contains exactly one script tag for push-subscribe.js
  - push-subscribe.js content defines key functions
  - pushSettings factory is exposed
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio


@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture()
def st_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def app(st_settings):
    _app = create_app()
    _app.dependency_overrides[get_settings] = lambda: st_settings
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture()
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# /static/js/push-subscribe.js is served (SC-9.1 equivalent)
# ---------------------------------------------------------------------------


async def test_push_subscribe_js_served(client: AsyncClient) -> None:
    """/static/js/push-subscribe.js returns 200 + JS content-type."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "javascript" in ct or "text" in ct


async def test_push_subscribe_js_contains_register_sw(client: AsyncClient) -> None:
    """push-subscribe.js defines registerSW function."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    assert "registerSW" in resp.text


async def test_push_subscribe_js_contains_subscribe_push(client: AsyncClient) -> None:
    """push-subscribe.js defines subscribePush function (SC-9.3 equivalent)."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    assert "subscribePush" in resp.text


async def test_push_subscribe_js_contains_unsubscribe_push(client: AsyncClient) -> None:
    """push-subscribe.js defines unsubscribePush function."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    assert "unsubscribePush" in resp.text


async def test_push_subscribe_js_contains_url_base64_helper(client: AsyncClient) -> None:
    """push-subscribe.js defines urlBase64ToUint8Array helper."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    assert "urlBase64ToUint8Array" in resp.text


async def test_push_subscribe_js_contains_push_settings(client: AsyncClient) -> None:
    """push-subscribe.js exposes window.pushSettings Alpine factory."""
    resp = await client.get("/static/js/push-subscribe.js")
    assert resp.status_code == 200
    assert "pushSettings" in resp.text


# ---------------------------------------------------------------------------
# base.html contains push-subscribe.js script tag (SC-9.2 equivalent)
# ---------------------------------------------------------------------------


async def test_base_html_includes_push_subscribe_js(client: AsyncClient) -> None:
    """GET /settings → base.html includes push-subscribe.js script tag (REQ-9.7)."""
    resp = await client.get("/settings")
    assert resp.status_code == 200
    assert "push-subscribe.js" in resp.text


async def test_base_html_push_subscribe_js_appears_once(client: AsyncClient) -> None:
    """push-subscribe.js script tag appears exactly once in base.html (REQ-9.8)."""
    resp = await client.get("/settings")
    assert resp.status_code == 200
    count = resp.text.count("push-subscribe.js")
    assert count == 1


async def test_base_html_has_apple_mobile_web_app_capable(client: AsyncClient) -> None:
    """base.html contains apple-mobile-web-app-capable meta tag (REQ-11.6)."""
    resp = await client.get("/settings")
    assert resp.status_code == 200
    assert "apple-mobile-web-app-capable" in resp.text
