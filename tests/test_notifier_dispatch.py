"""Red tests for WU-4 — notifier.dispatch orchestrator.

Tests run BEFORE dispatch is implemented. All must fail until green commit lands.

Covers:
  - should_notify=False → send_push NOT called
  - should_notify=True → asyncio.create_task scheduled with send_push;
    drain with await asyncio.sleep(0) before asserting
  - send_push raises inside task → dispatch returns None (no propagation)
  - Malformed/unknown event type → dispatch returns None (should_notify returns False)
  - dispatch itself never raises even if internal code throws
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from claude_remote.db.events import Event
from claude_remote.db.notifications import NotificationPreferences
from claude_remote.db.projects import Project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str, payload: str = "{}") -> Event:
    return Event(
        id="evt-dispatch-001",
        instance_id=None,
        project_id=None,
        event_type=event_type,
        payload=payload,
        received_at="2026-01-01T00:00:00+00:00",
    )


def _make_project() -> Project:
    return Project(
        id="proj-001",
        name="myproject",
        slug="myproject",
        path="/home/user/sandbox/myproject",
        domain="sandbox",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_prefs(*, notify_on_notification: bool = True) -> NotificationPreferences:
    return NotificationPreferences(
        notify_on_notification=notify_on_notification,
        notify_on_stop=False,
        notify_on_session_end=False,
        notify_on_session_start=False,
        notify_on_pre_tool_use=False,
        notify_on_post_tool_use=False,
        quiet_hours_start=None,
        quiet_hours_end=None,
        ntfy_topic="test-topic",
        updated_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# dispatch — should_notify=False → no push
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_no_push_when_toggle_disabled() -> None:
    """dispatch() with toggle disabled → send_push is NOT called."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=False)
    event = _make_event("Notification")
    project = _make_project()

    send_push_mock = AsyncMock()
    with patch.object(notifier, "send_push", send_push_mock):
        await notifier.dispatch(event, project, prefs)
        await asyncio.sleep(0)  # drain any pending tasks

    send_push_mock.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch — should_notify=True → task scheduled
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_creates_task_when_toggle_enabled() -> None:
    """dispatch() with toggle enabled → send_push IS called (via task)."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()

    send_push_mock = AsyncMock(return_value=None)
    with patch.object(notifier, "send_push", send_push_mock):
        await notifier.dispatch(event, project, prefs)
        await asyncio.sleep(0)  # drain the scheduled task

    send_push_mock.assert_called_once()


@pytest.mark.anyio
async def test_dispatch_passes_correct_args_to_send_push() -> None:
    """dispatch() must pass (event, project, prefs) to send_push."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()

    send_push_mock = AsyncMock(return_value=None)
    with patch.object(notifier, "send_push", send_push_mock):
        await notifier.dispatch(event, project, prefs)
        await asyncio.sleep(0)

    call_kwargs = send_push_mock.call_args
    assert call_kwargs is not None
    # Positional args should be event, project, prefs
    args = call_kwargs.args
    assert args[0] is event
    assert args[1] is project
    assert args[2] is prefs


# ---------------------------------------------------------------------------
# dispatch — send_push raises → dispatch swallows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_swallows_send_push_exception() -> None:
    """When send_push raises, dispatch returns None without propagating."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()

    send_push_mock = AsyncMock(side_effect=RuntimeError("ntfy exploded"))
    with patch.object(notifier, "send_push", send_push_mock):
        # dispatch itself must not raise
        result = await notifier.dispatch(event, project, prefs)
        await asyncio.sleep(0)

    assert result is None


# ---------------------------------------------------------------------------
# dispatch — unknown event type (should_notify returns False)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_unknown_event_type_returns_none() -> None:
    """dispatch() with unknown event_type → returns None (should_notify=False → no task)."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("UnknownEventType")
    project = _make_project()

    send_push_mock = AsyncMock()
    with patch.object(notifier, "send_push", send_push_mock):
        result = await notifier.dispatch(event, project, prefs)
        await asyncio.sleep(0)

    assert result is None
    send_push_mock.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch — never raises even when internal code throws
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_never_raises_on_internal_error() -> None:
    """dispatch() must return None even if should_notify itself raises (invariant)."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()

    with patch.object(notifier, "should_notify", side_effect=RuntimeError("boom")):
        result = await notifier.dispatch(event, project, prefs)

    assert result is None
