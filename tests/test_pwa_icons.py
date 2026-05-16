"""PWA icons (roadmap follow-up: installed PWA must use the new bot icon).

Android caches the install-time icon and is unreliable with SVG home-screen
icons — it needs PNG 192/512 plus a maskable variant. The manifest must
reference those, the files must be served, and the manifest <link> must be
cache-busted so Chrome refetches it.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio


@pytest.fixture()
def _settings(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db, MIGRATIONS_DIR)
    root = tmp_path / "p"
    root.mkdir()
    return Settings(db_path=db, projects_root=root)


@pytest.fixture()
def app(_settings):
    a = create_app()
    a.dependency_overrides[get_settings] = lambda: _settings
    yield a
    a.dependency_overrides.clear()


@pytest.fixture()
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_manifest_lists_png_icons(client: AsyncClient) -> None:
    resp = await client.get("/static/manifest.json")
    assert resp.status_code == 200
    icons = json.loads(resp.text)["icons"]
    srcs = {i["src"] for i in icons}
    assert "/static/icon-192.png" in srcs
    assert "/static/icon-512.png" in srcs
    png = [i for i in icons if i["src"].endswith(".png")]
    assert all(i["type"] == "image/png" for i in png)
    sizes = {i["sizes"] for i in png}
    assert "192x192" in sizes and "512x512" in sizes


async def test_manifest_has_maskable_icon(client: AsyncClient) -> None:
    resp = await client.get("/static/manifest.json")
    icons = json.loads(resp.text)["icons"]
    assert any("maskable" in i.get("purpose", "") for i in icons)


@pytest.mark.parametrize(
    "path", ["/static/icon-192.png", "/static/icon-512.png", "/static/icon-maskable-512.png"]
)
async def test_icon_files_served_as_png(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")


async def test_manifest_link_is_cache_busted(client: AsyncClient) -> None:
    """base.html manifest <link> carries ?v= so Chrome refetches the manifest."""
    resp = await client.get("/")
    assert resp.status_code == 200
    import re

    assert re.search(r'rel="manifest" href="/static/manifest\.json\?v=\d+"', resp.text)
