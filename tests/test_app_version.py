"""App version derived from git tags.

The header version must reflect the real git state (tag when tagged, short
SHA otherwise) instead of a hardcoded string. The helper must never raise.
"""

from __future__ import annotations

import subprocess

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


def test_app_version_helper_returns_nonempty() -> None:
    from claude_remote.routes._templates import app_version

    v = app_version()
    assert isinstance(v, str)
    assert v.strip() != ""


def test_app_version_matches_git_describe() -> None:
    """When git is available the helper equals git describe --tags --always --dirty."""
    from claude_remote.routes._templates import app_version

    expected = subprocess.run(
        ["git", "describe", "--tags", "--always", "--dirty"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert expected, "git describe produced no output in this checkout"
    assert app_version() == expected


def test_app_version_never_raises(monkeypatch) -> None:
    """If the git subprocess blows up, the helper falls back, never raises."""
    import claude_remote.routes._templates as tmpl

    def _boom(*_a, **_k):
        raise OSError("git not found")

    monkeypatch.setattr(tmpl.subprocess, "run", _boom)
    tmpl.app_version.cache_clear()
    try:
        v = tmpl.app_version()
        assert isinstance(v, str)
        assert v.strip() != ""
    finally:
        tmpl.app_version.cache_clear()


async def test_home_header_shows_dynamic_version_not_hardcoded(
    client: AsyncClient,
) -> None:
    """Home header renders the computed version, not the literal 'v0.1'."""
    from claude_remote.routes._templates import app_version

    resp = await client.get("/")
    assert resp.status_code == 200
    assert app_version() in resp.text
    # The old hardcoded token must be gone (unless a real tag is literally v0.1).
    assert ">v0.1 · py<" not in resp.text
