"""First-run /setup screen + not-configured redirect guard (cfgroot WU-2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio


@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


def _settings(tmp_db, projects_root, *, configured: bool) -> Settings:
    return Settings(
        db_path=tmp_db, projects_root=Path(projects_root), configured=configured
    )


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    )


async def test_unconfigured_redirects_to_setup(tmp_db, tmp_path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=False
    )
    async with _client(app) as c:
        r = await c.get("/")
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


async def test_setup_page_explains_model_and_suggests_path(tmp_db, tmp_path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=False
    )
    async with _client(app) as c:
        r = await c.get("/setup")
    assert r.status_code == 200
    body = r.text.lower()
    assert "domain" in body and "project" in body  # root → domain → project
    assert 'name="path"' in r.text  # the path input


async def test_setup_post_existing_path_persists(tmp_db, tmp_path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=False
    )
    async with _client(app) as c:
        r = await c.post("/ui/setup", data={"path": str(root)})
    assert r.status_code in (200, 303)
    from claude_remote.db.app_settings import AppSettingsRepository

    stored = AppSettingsRepository(lambda: get_connection_for(tmp_db)).get()
    assert stored.projects_root == str(root)


async def test_setup_post_missing_path_warns_then_creates(tmp_db, tmp_path) -> None:
    target = tmp_path / "newroot"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=False
    )
    async with _client(app) as c:
        warn = await c.post("/ui/setup", data={"path": str(target)})
        assert warn.status_code == 200
        assert "create" in warn.text.lower()  # offered to create it
        assert not target.exists()

        ok = await c.post(
            "/ui/setup", data={"path": str(target), "confirm_create": "1"}
        )
    assert ok.status_code in (200, 303)
    assert target.is_dir()
    from claude_remote.db.app_settings import AppSettingsRepository

    assert (
        AppSettingsRepository(lambda: get_connection_for(tmp_db)).get().projects_root
        == str(target)
    )


async def test_configured_does_not_redirect(tmp_db, tmp_path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=True
    )
    async with _client(app) as c:
        r = await c.get("/")
    assert r.status_code == 200


async def test_static_exempt_from_guard(tmp_db, tmp_path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_db, tmp_path, configured=False
    )
    async with _client(app) as c:
        r = await c.get("/static/css/app.css")
    assert r.status_code == 200  # not redirected to /setup
