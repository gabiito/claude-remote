"""Session cookie sign/verify + /login + /logout (auth WU-2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.app_settings import AppSettingsRepository
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio


# --- pure sign/verify ---


def test_session_sign_verify_roundtrip() -> None:
    from claude_remote.services.auth import sign_session, verify_session

    tok = sign_session("secret-A", now=1000)
    assert verify_session("secret-A", tok, now=1000) is True


def test_session_rejects_tamper_wrong_secret_and_expiry() -> None:
    from claude_remote.services.auth import sign_session, verify_session

    tok = sign_session("secret-A", now=1000)
    assert verify_session("secret-B", tok, now=1000) is False  # wrong secret
    assert verify_session("secret-A", tok + "x", now=1000) is False  # tampered
    assert verify_session("secret-A", "garbage", now=1000) is False
    # default TTL is long but finite — far future must expire
    assert verify_session("secret-A", tok, now=1000 + 10**9) is False


# --- HTTP /login /logout ---


@pytest.fixture()
def db_path(tmp_path):
    p = tmp_path / "auth.db"
    apply_migrations(p, MIGRATIONS_DIR)
    repo = AppSettingsRepository(lambda: get_connection_for(p))
    from claude_remote.services.auth import hash_password

    repo.set_password_hash(hash_password("letmein"))
    repo.get_or_create_session_secret()
    return p


@pytest.fixture()
def client(db_path):
    settings = Settings(db_path=db_path, projects_root=db_path.parent)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    return AsyncClient(transport=transport, base_url="http://test")


async def test_get_login_page(client) -> None:
    async with client as c:
        r = await c.get("/login")
    assert r.status_code == 200
    assert "password" in r.text.lower()


async def test_login_page_is_styled_and_self_contained(client) -> None:
    async with client as c:
        r = await c.get("/login")
        js = await c.get("/static/js/login.js")
    html = r.text
    assert "cr-login" in html  # styled shell, not the old bare card
    assert 'class="cr-brand' in html  # reuses the app brand mark
    assert "cr-pw-toggle" in html  # show/hide eye
    assert "js/login.js" in html
    # CSP-safe: the toggle is an external script, never inline.
    assert "<script>" not in html
    assert js.status_code == 200
    assert "addEventListener" in js.text


async def test_post_login_wrong_password_no_cookie(client) -> None:
    async with client as c:
        r = await c.post("/login", data={"password": "nope"})
    assert "cr_session" not in r.headers.get("set-cookie", "")
    assert r.status_code in (200, 401)


async def test_post_login_correct_sets_cookie_and_redirects(client) -> None:
    async with client as c:
        r = await c.post(
            "/login", data={"password": "letmein"}, follow_redirects=False
        )
    sc = r.headers.get("set-cookie", "")
    assert "cr_session=" in sc
    assert "HttpOnly" in sc
    # SameSite value is case-insensitive per RFC 6265bis; Starlette emits 'lax'.
    assert "samesite=lax" in sc.lower()
    assert r.status_code in (302, 303)


async def test_logout_clears_cookie(client) -> None:
    async with client as c:
        await c.post("/login", data={"password": "letmein"})
        r = await c.post("/logout", follow_redirects=False)
    sc = r.headers.get("set-cookie", "")
    assert "cr_session=" in sc and ("Max-Age=0" in sc or 'cr_session=""' in sc)
    assert r.status_code in (302, 303)
