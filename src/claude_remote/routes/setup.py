"""First-run setup — choose the projects root (cfgroot WU-2).

GET /setup explains the root → domain → project model and suggests the
detected default. POST /ui/setup validates: existing dir → persist; missing
→ warn + offer create/correct; file → error. Once persisted, the
not-configured guard stops redirecting here.
"""

from __future__ import annotations

import os
from pathlib import Path

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


def _persist(settings: Settings, path: Path) -> Response:
    AppSettingsRepository(
        lambda: get_connection_for(settings.db_path)
    ).set_projects_root(str(path))
    return RedirectResponse("/", status_code=303, headers={"HX-Redirect": "/"})


@router.post("/ui/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    path: str = Form(default=""),
    confirm_create: str = Form(default=""),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Response:
    raw = (path or "").strip()
    if not raw:
        return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
            request, "partials/setup_result.html",
            {"error": "Enter a folder path."},
        )
    p = Path(raw).expanduser()

    if p.exists():
        if not p.is_dir():
            return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
                request, "partials/setup_result.html",
                {"error": f"{p} exists but is not a directory."},
            )
        return _persist(settings, p.resolve())

    if confirm_create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
                request, "partials/setup_result.html",
                {"error": f"Could not create {p}: {exc}"},
            )
        return _persist(settings, p.resolve())

    # Doesn't exist yet — warn + offer to create or correct.
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request, "partials/setup_result.html", {"create_path": str(p)}
    )
