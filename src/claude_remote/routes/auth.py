"""Login / logout (auth/#7).

Single shared password (set via `claudio set-password`) → HMAC-signed
session cookie. No server-side session store. /login and /logout are
exempt from the auth gate (see app.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from claude_remote.config import Settings, get_settings
from claude_remote.db.app_settings import AppSettingsRepository
from claude_remote.db.connection import get_connection_for
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.services.auth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    sign_session,
    verify_password,
    verify_session,
)

router = APIRouter(tags=["auth"])


def _repo(settings: Settings) -> AppSettingsRepository:
    return AppSettingsRepository(lambda: get_connection_for(settings.db_path))


def has_valid_session(request: Request, secret: str | None) -> bool:
    if not secret:
        return False
    tok = request.cookies.get(COOKIE_NAME)
    return bool(tok) and verify_session(secret, tok)


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Response:
    s = _repo(settings).get()
    if has_valid_session(request, s.session_secret):
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request,
        "login.html",
        {"error": None, "configured": s.password_hash is not None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    password: str = Form(default=""),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Response:
    repo = _repo(settings)
    s = repo.get()
    if s.password_hash is None or not verify_password(password, s.password_hash):
        return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
            request,
            "login.html",
            {
                "error": "Wrong password."
                if s.password_hash
                else "No password set — run: claudio set-password",
                "configured": s.password_hash is not None,
            },
            status_code=401,
        )
    secret = repo.get_or_create_session_secret()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        sign_session(secret),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp
