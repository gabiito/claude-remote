"""Security headers + CSRF origin check + docs behind auth (auth WU-4)."""

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


@pytest.fixture()
def settings(tmp_path):
    db = tmp_path / "h.db"
    apply_migrations(db, MIGRATIONS_DIR)
    root = tmp_path / "p"
    root.mkdir()
    repo = AppSettingsRepository(lambda: get_connection_for(db))
    repo.set_password_hash(hash_password("pw"))
    repo._secret = repo.get_or_create_session_secret()  # type: ignore[attr-defined]
    return Settings(db_path=db, projects_root=root)


def _client(settings: Settings) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


def _cookie(settings: Settings) -> str:
    secret = AppSettingsRepository(
        lambda: get_connection_for(settings.db_path)
    ).get().session_secret
    assert secret
    return sign_session(secret)


async def test_security_headers_present(settings) -> None:
    async with _client(settings) as c:
        r = await c.get("/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Referrer-Policy" in r.headers
    assert "Content-Security-Policy" in r.headers
    # Google Fonts must not be CSP-blocked: the stylesheet origin in
    # style-src and the font-file origin via an explicit font-src.
    csp = r.headers["Content-Security-Policy"]
    assert "https://fonts.googleapis.com" in csp, (
        "style-src must allow the Google Fonts stylesheet origin"
    )
    assert "font-src" in csp and "https://fonts.gstatic.com" in csp, (
        "font-src must allow the Google Fonts file origin (fonts.gstatic.com)"
    )


async def test_cross_origin_post_blocked(settings) -> None:
    async with _client(settings) as c:
        r = await c.post(
            "/login",
            data={"password": "pw"},
            headers={"Origin": "http://evil.example"},
            follow_redirects=False,
        )
    assert r.status_code == 403


async def test_same_origin_post_allowed(settings) -> None:
    async with _client(settings) as c:
        r = await c.post(
            "/login",
            data={"password": "pw"},
            headers={"Origin": "http://test"},
            follow_redirects=False,
        )
    assert r.status_code in (302, 303)  # not 403


async def test_no_origin_post_allowed(settings) -> None:
    """Claude's hook receiver (server-to-server) sends no Origin."""
    async with _client(settings) as c:
        r = await c.post("/hooks/Notification?token=bogus", follow_redirects=False)
    assert r.status_code == 200


async def test_docs_behind_auth(settings) -> None:
    async with _client(settings) as c:
        unauth = await c.get("/docs", follow_redirects=False)
        c.cookies.set("cr_session", _cookie(settings))
        authed = await c.get("/docs", follow_redirects=False)
    assert unauth.status_code in (302, 303)
    assert unauth.headers["location"] == "/login"
    assert authed.status_code == 200
