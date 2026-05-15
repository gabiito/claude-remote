"""ntfy-based push notifier. Pure decision + async HTTP egress. Never raises.

Module is extended across WU-2, WU-3, WU-4 in strict order:
  WU-2: constants + _parse_time + _in_quiet_hours + should_notify
  WU-3: _build_body + send_push (+ httpx runtime dep)
  WU-4: dispatch
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

import httpx

from claude_remote.db.events import Event
from claude_remote.db.notifications import NotificationPreferences
from claude_remote.db.projects import Project
from claude_remote.services.event_snippet import extract_snippet

if TYPE_CHECKING:
    from httpx import AsyncClient

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
# Event-type → ntfy priority
# ---------------------------------------------------------------------------

EVENT_TYPE_TO_PRIORITY: dict[str, str] = {
    "Notification": "urgent",
    "Stop": "default",
    "SessionEnd": "default",
    "SessionStart": "low",
    "PreToolUse": "low",
    "PostToolUse": "low",
}

# ---------------------------------------------------------------------------
# Event-type → ntfy tag emoji
# ---------------------------------------------------------------------------

EVENT_TYPE_TO_TAGS: dict[str, str] = {
    "Notification": "bell",
    "Stop": "octagonal_sign",
    "SessionEnd": "checkered_flag",
    "SessionStart": "rocket",
    "PreToolUse": "hourglass_flowing_sand",
    "PostToolUse": "white_check_mark",
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
# _build_body — compose the ntfy push body per event type
# ---------------------------------------------------------------------------


def _build_body(event: Event, project: Project) -> str:
    """Compose the push notification body.

    For Notification events: extract message from payload (via extract_snippet).
    For other events: use the canned template, substituting project_name / tool_name.
    All output is capped at _MAX_BODY chars.
    Never raises.
    """
    et = event.event_type

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
# send_push — async ntfy POST (never raises)
# ---------------------------------------------------------------------------


async def send_push(
    event: Event,
    project: Project,
    prefs: NotificationPreferences,
    *,
    http_client: AsyncClient | None = None,
) -> None:
    """POST an event push to ntfy.sh/{prefs.ntfy_topic}.

    On ANY exception (network, timeout, HTTP error, JSON error): log a WARNING
    and return None. MUST NOT raise.

    Args:
        event: the event to push.
        project: the project owning the event (used in Title and body templates).
        prefs: notification preferences (ntfy_topic, etc.).
        http_client: optional pre-built AsyncClient (for test injection via respx).
            When None, a fresh per-call client is opened with timeout=5.0s.
    """
    try:
        title = f"{project.domain}/{project.name}"
        body = _build_body(event, project)
        url = f"https://ntfy.sh/{prefs.ntfy_topic}"
        headers = {
            "Title": title,
            "Priority": EVENT_TYPE_TO_PRIORITY.get(event.event_type, "default"),
            "Tags": EVENT_TYPE_TO_TAGS.get(event.event_type, "bell"),
        }

        if http_client is None:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, content=body.encode(), headers=headers)
        else:
            response = await http_client.post(url, content=body.encode(), headers=headers)

        if response.status_code >= 400:
            logger.warning(
                "ntfy push returned HTTP %d for event %s", response.status_code, event.id
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning("ntfy push failed for event %s: %s", event.id, exc)


# ---------------------------------------------------------------------------
# dispatch — orchestrator (fire-and-forget, never raises)
# ---------------------------------------------------------------------------


async def dispatch(
    event: Event,
    project: Project,
    prefs: NotificationPreferences,
    *,
    http_client: AsyncClient | None = None,
) -> None:
    """Decide whether to push and schedule send_push as a background task.

    Decision: calls should_notify with the current UTC time. If False, returns
    immediately. If True, schedules send_push via asyncio.create_task so the
    caller returns without waiting for the network call.

    MUST NOT raise under any circumstance. All exceptions are logged and
    swallowed so the hook handler's never-raise contract is preserved.

    Args:
        event: the persisted event record.
        project: the project owning the event.
        prefs: current notification preferences.
        http_client: optional AsyncClient for test injection.
    """
    try:
        # Use local wall-clock for quiet-hours comparison (matches user's HH:MM input).
        now = datetime.now()
        if not should_notify(event, prefs, now=now):
            return
        asyncio.create_task(send_push(event, project, prefs, http_client=http_client))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifier dispatch failed for event %s: %s", event.id, exc)
