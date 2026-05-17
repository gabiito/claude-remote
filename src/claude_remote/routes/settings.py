"""Settings page router — GET /settings + POST /ui/settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.notifications import NotificationsRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.services.notifier import _parse_time  # type: ignore[reportPrivateUsage]

router = APIRouter(tags=["settings"])


def get_notifications_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> NotificationsRepository:
    return NotificationsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    notifications_repo: NotificationsRepository = Depends(get_notifications_repo),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    prefs = notifications_repo.get()
    return TEMPLATES.TemplateResponse(
        request,
        "settings.html",
        {"prefs": prefs, "projects_root": str(settings.projects_root)},
    )


@router.post("/ui/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    notify_on_notification: bool = Form(default=False),  # noqa: B008
    notify_on_stop: bool = Form(default=False),  # noqa: B008
    notify_on_session_end: bool = Form(default=False),  # noqa: B008
    notify_on_session_start: bool = Form(default=False),  # noqa: B008
    notify_on_pre_tool_use: bool = Form(default=False),  # noqa: B008
    notify_on_post_tool_use: bool = Form(default=False),  # noqa: B008
    quiet_hours_start: str = Form(default=""),  # noqa: B008
    quiet_hours_end: str = Form(default=""),  # noqa: B008
    notifications_repo: NotificationsRepository = Depends(get_notifications_repo),  # noqa: B008
) -> HTMLResponse:
    qh_start = (quiet_hours_start or "").strip() or None
    qh_end = (quiet_hours_end or "").strip() or None

    for label, value in (("inicio", qh_start), ("fin", qh_end)):
        if value is not None and _parse_time(value) is None:
            content = TEMPLATES.get_template("partials/error_message.html").render(  # type: ignore[attr-defined]
                message=f"Formato inválido en quiet hours ({label}); usá HH:MM."
            )
            return HTMLResponse(
                content=content,
                status_code=400,
                headers={"HX-Reswap": "innerHTML", "HX-Retarget": "#settings-toast"},
            )

    notifications_repo.update(
        notify_on_notification=notify_on_notification,
        notify_on_stop=notify_on_stop,
        notify_on_session_end=notify_on_session_end,
        notify_on_session_start=notify_on_session_start,
        notify_on_pre_tool_use=notify_on_pre_tool_use,
        notify_on_post_tool_use=notify_on_post_tool_use,
        quiet_hours_start=qh_start,
        quiet_hours_end=qh_end,
    )

    content = TEMPLATES.get_template("partials/settings_toast.html").render()  # type: ignore[attr-defined]
    return HTMLResponse(
        content=content,
        status_code=200,
        headers={"HX-Trigger": "settings-saved"},
    )
