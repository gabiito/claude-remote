"""Web-push notifier. Pure decision + fire-and-forget egress via web_push.send_to_all.

ntfy egress removed in WU-5. dispatch() now calls web_push.send_to_all.
should_notify, _build_body, and quiet-hours helpers are UNCHANGED.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

from claude_remote.db.events import Event
from claude_remote.db.notifications import NotificationPreferences
from claude_remote.db.projects import Project
from claude_remote.services import web_push
from claude_remote.services.event_snippet import extract_snippet

if TYPE_CHECKING:
    from claude_remote.db.push_subscriptions import PushSubscriptionsRepository
    from claude_remote.db.vapid_keys import VapidKeysRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-type → preference toggle mapping
# ---------------------------------------------------------------------------

EVENT_TYPE_TO_TOGGLE: dict[str, str] = {
    "Notification": "notify_on_notification",
    "Stop": "notify_on_stop",
    "SessionEnd": "notify_on_session_end",
    "SessionStart": "notify_on_session_start",
    "PreToolUse": "notify_on_pre_tool_use",
    "PostToolUse": "notify_on_post_tool_use",
}

# ---------------------------------------------------------------------------
# Event-type → canned body template (non-Notification events)
# ---------------------------------------------------------------------------

EVENT_TYPE_TO_BODY_TEMPLATE: dict[str, str] = {
    "Stop": "Claude stopped on {project_name}",
    "SessionEnd": "Session ended on {project_name}",
    "SessionStart": "Session started on {project_name}",
    "PreToolUse": "Claude is using {tool_name}",
    "PostToolUse": "Claude finished {tool_name}",
}

_MAX_BODY = 1000

# AskUserQuestion arrives as a PreToolUse event but is semantically an
# input-needed moment (Claude is blocked waiting on the user), identical in
# intent to a Notification. It rides the notify_on_notification toggle.
_ASK_TOOL_NAME = "AskUserQuestion"


def _is_ask_user_question(event: Event) -> bool:
    """True when the event is a PreToolUse for the AskUserQuestion tool.

    Reuses extract_snippet, which for PreToolUse returns the tool name and
    never raises on malformed payloads.
    """
    return (
        event.event_type == "PreToolUse"
        and extract_snippet(event, max_length=64) == _ASK_TOOL_NAME
    )


# ---------------------------------------------------------------------------
# Quiet hours helpers (pure, never raise)
# ---------------------------------------------------------------------------


def _parse_time(s: str | None) -> time | None:
    """Parse an HH:MM string to a time object. Returns None on any error."""
    if not s:
        return None
    try:
        parts = s.split(":")
        if len(parts) != 2:
            return None
        hh, mm = int(parts[0]), int(parts[1])
        return time(hour=hh, minute=mm)
    except (ValueError, AttributeError):
        return None


def _in_quiet_hours(now: time, start: time, end: time) -> bool:
    """Return True when now falls within the quiet hours window [start, end).

    Handles same-day and overnight (wrap-around) windows:
      - start == end → zero-duration → not quiet (False)
      - start < end  → same-day window: start <= now < end
      - start > end  → overnight wrap: now >= start OR now < end
    """
    if start == end:
        return False
    if start < end:
        # Same-day window (e.g. 09:00–17:00): inclusive start, exclusive end
        return start <= now < end
    # Overnight wrap (e.g. 23:00–07:00): from start to midnight + midnight to end
    return now >= start or now < end


# ---------------------------------------------------------------------------
# should_notify — pure decision function
# ---------------------------------------------------------------------------


def should_notify(
    event: Event,
    prefs: NotificationPreferences,
    *,
    now: datetime,
) -> bool:
    """Return True when the event should trigger a push notification.

    Decision order:
      1. If event_type has no corresponding toggle → False.
      2. If the toggle is disabled in prefs → False.
      3. If both quiet_hours_start/end are set and parseable, and now is inside
         the quiet window → False.
      4. Otherwise → True.

    Args:
        event: the event record whose event_type determines which toggle to check.
        prefs: current notification preferences singleton.
        now: UTC-aware datetime used to evaluate quiet hours. The function
             converts to server-local via .astimezone().

    Returns:
        bool — True means "dispatch the push", False means "suppress".

    This function is pure: no I/O, no side effects. Quiet hours errors fail open.
    """
    if _is_ask_user_question(event):
        toggle_field: str | None = "notify_on_notification"
    else:
        toggle_field = EVENT_TYPE_TO_TOGGLE.get(event.event_type)
    if toggle_field is None:
        return False

    if not getattr(prefs, toggle_field):
        return False

    # Quiet hours check — caller passes the relevant wall-clock datetime.
    # The function uses .time() directly so timezone conversion is the caller's
    # responsibility (dispatch() passes datetime.now(), which is local-naive).
    if prefs.quiet_hours_start and prefs.quiet_hours_end:
        start = _parse_time(prefs.quiet_hours_start)
        end = _parse_time(prefs.quiet_hours_end)
        if start is not None and end is not None and _in_quiet_hours(now.time(), start, end):
            return False

    return True


# ---------------------------------------------------------------------------
# _build_body — compose the push body per event type
# ---------------------------------------------------------------------------


def _build_body(event: Event, project: Project) -> str:
    """Compose the push notification body.

    For Notification events: extract message from payload (via extract_snippet).
    For other events: use the canned template, substituting project_name / tool_name.
    All output is capped at _MAX_BODY chars.
    Never raises.
    """
    et = event.event_type

    if _is_ask_user_question(event):
        return f"Claude is asking you a question on {project.domain}/{project.name}"

    if et == "Notification":
        body = extract_snippet(event, max_length=_MAX_BODY)
        if not body:
            body = f"Notification from {project.domain}/{project.name}"
        return body[:_MAX_BODY]

    template = EVENT_TYPE_TO_BODY_TEMPLATE.get(et, "")
    if not template:
        return ""

    tool_name = (
        extract_snippet(event, max_length=80) if et in ("PreToolUse", "PostToolUse") else ""
    )
    return template.format(
        project_name=f"{project.domain}/{project.name}",
        tool_name=tool_name or "una herramienta",
    )[:_MAX_BODY]


# ---------------------------------------------------------------------------
# dispatch — orchestrator (fire-and-forget, never raises)
# ---------------------------------------------------------------------------


async def dispatch(
    event: Event,
    project: Project,
    prefs: NotificationPreferences,
    *,
    subscriptions_repo: PushSubscriptionsRepository,
    vapid_repo: VapidKeysRepository,
) -> None:
    """Decide whether to push and schedule web_push.send_to_all as a background task.

    Decision: calls should_notify with the current local wall-clock time.
    If False, returns immediately. If True, schedules send_to_all via
    asyncio.create_task (fire-and-forget, double-detach — see ADR-14).

    MUST NOT raise under any circumstance. All exceptions are logged and
    swallowed so the hook handler's never-raise contract is preserved.

    Args:
        event: the persisted event record.
        project: the project owning the event.
        prefs: current notification preferences.
        subscriptions_repo: repo for push subscriptions (DI-injected by hooks.py).
        vapid_repo: repo for VAPID keys (DI-injected by hooks.py).
    """
    try:
        # Use local wall-clock for quiet-hours comparison (matches user's HH:MM input).
        now = datetime.now()
        if not should_notify(event, prefs, now=now):
            return

        title = f"{project.domain}/{project.name}"
        body = _build_body(event, project)
        data = {
            "url": f"/projects/{project.id}",
            "event_type": event.event_type,
        }

        asyncio.create_task(
            web_push.send_to_all(
                subscriptions_repo,
                vapid_repo,
                title=title,
                body=body,
                data=data,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifier dispatch failed for event %s: %s", event.id, exc)
