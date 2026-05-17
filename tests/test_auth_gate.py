"""Global auth gate (auth WU-3).

Self-contained clients (own DB + settings) so these pass once the gate
works, independent of the shared conftest fixtures (which WU-5 teaches to
log in for the rest of the suite).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.app_settings import AppSettingsRepository
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.services.auth import hash_password, sign_session

pytestmark = pytest.mark.anyio


def _client(settings: Settings) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


@pytest.fixture()
def settings(tmp_path):
    db = tmp_path / "gate.db"
    apply_migrations(db, MIGRATIONS_DIR)
    root = tmp_path / "projects"
    root.mkdir()
    return Settings(db_path=db, projects_root=root)


def _set_pw(settings: Settings, pw: str) -> str:
    repo = AppSettingsRepository(lambda: get_connection_for(settings.db_path))
    repo.set_password_hash(hash_password(pw))
    return repo.get_or_create_session_secret()


async def test_no_password_redirects_to_login(settings) -> None:
    async with _client(settings) as c:
        r = await c.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/login"


async def test_password_set_but_no_cookie_redirects_to_login(settings) -> None:
    _set_pw(settings, "pw")
    async with _client(settings) as c:
        r = await c.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/login"


async def test_valid_cookie_passes_gate(settings) -> None:
    secret = _set_pw(settings, "pw")
    async with _client(settings) as c:
        c.cookies.set("cr_session", sign_session(secret))
        r = await c.get("/", follow_redirects=False)
    # Through the gate: not bounced to /login (200, or the configured-guard
    # redirect — but never /login).
    assert r.headers.get("location") != "/login"
    assert r.status_code == 200


async def test_exempt_paths_open_without_auth(settings) -> None:
    _set_pw(settings, "pw")
    async with _client(settings) as c:
        assert (await c.get("/login")).status_code == 200
        assert (await c.get("/health")).status_code == 200
        # Claude's hook receiver is token-gated, never login-gated.
        h = await c.post("/hooks/Notification?token=bogus", follow_redirects=False)
        assert h.status_code == 200
        assert h.headers.get("location") != "/login"


async def test_sse_unauth_is_401_not_redirect(settings) -> None:
    _set_pw(settings, "pw")
    async with _client(settings) as c:
        r = await c.get("/sse/home", follow_redirects=False)
    assert r.status_code == 401
