"""First-run setup — choose the projects root (cfgroot WU-2).

GET /setup explains the root → domain → project model and suggests the
detected default. POST /ui/setup validates: existing dir → persist; missing
→ warn + offer create/correct; file → error. Once persisted, the
not-configured guard stops redirecting here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from claude_remote.config import Settings, get_settings
from claude_remote.db.app_settings import AppSettingsRepository
from claude_remote.db.connection import get_connection_for
from claude_remote.routes._templates import templates as TEMPLATES

router = APIRouter(tags=["setup"])


def _suggested() -> tuple[str, bool]:
    raw = os.environ.get("CLAUDE_REMOTE_PROJECTS_ROOT", "~/Projects")
    p = Path(raw).expanduser()
    return str(p), (p.exists() and p.is_dir())


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    suggested, exists = _suggested()
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request,
        "setup.html",
        {"suggested": suggested, "suggested_exists": exists},
    )


def _persist(request: Request, settings: Settings, path: Path) -> Response:
    AppSettingsRepository(
        lambda: get_connection_for(settings.db_path)
    ).set_projects_root(str(path))
    # htmx follows a 303 in the XHR (the home HTML would land in the target).
    # Use HX-Redirect on a 200 so htmx does a real client-side navigation.
    if request.headers.get("hx-request"):
        return HTMLResponse(status_code=200, headers={"HX-Redirect": "/"})
    return RedirectResponse("/", status_code=303)


def _resolve(raw: str, confirm_create: str) -> tuple[str, Any]:
    """Validate a candidate root.

    Returns ("ok", resolved Path) | ("warn", {"create_path": str})
            | ("error", {"error": msg}).
    """
    raw = (raw or "").strip()
    if not raw:
        return "error", {"error": "Enter a folder path."}
    p = Path(raw).expanduser()
    if p.exists():
        if not p.is_dir():
            return "error", {"error": f"{p} exists but is not a directory."}
        return "ok", p.resolve()
    if confirm_create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return "error", {"error": f"Could not create {p}: {exc}"}
        return "ok", p.resolve()
    return "warn", {"create_path": str(p)}


@router.post("/ui/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    path: str = Form(default=""),
    confirm_create: str = Form(default=""),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Response:
    kind, payload = _resolve(path, confirm_create)
    if kind == "ok":
        return _persist(request, settings, payload)
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request, "partials/setup_result.html", payload
    )


@router.post("/ui/settings/projects-root", response_class=HTMLResponse)
async def settings_projects_root(
    request: Request,
    path: str = Form(default=""),
    confirm_create: str = Form(default=""),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Change the root from /settings — same validation, but stay on the page
    with a confirmation instead of redirecting home."""
    kind, payload = _resolve(path, confirm_create)
    if kind == "ok":
        AppSettingsRepository(
            lambda: get_connection_for(settings.db_path)
        ).set_projects_root(str(payload))
        return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
            request, "partials/setup_result.html", {"saved_path": str(payload)}
        )
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request, "partials/setup_result.html", payload
    )
