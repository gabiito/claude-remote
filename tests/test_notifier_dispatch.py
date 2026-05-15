"""Tests for WU-5 — notifier.dispatch with web push egress.

Replaces the old ntfy-based dispatch tests. All references to send_push (ntfy)
are removed. dispatch now calls web_push.send_to_all (fire-and-forget).

Covers (REQ-12.5, SC-6.1–6.3):
  - dispatch calls web_push.send_to_all when should_notify is True
  - dispatch does NOT call web_push.send_to_all when should_notify is False
  - dispatch passes correct title/body/data to send_to_all
  - dispatch never raises even if send_to_all raises
  - dispatch returns None for unknown event_type (should_notify=False)
  - dispatch is fire-and-forget: schedules via asyncio.create_task
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
        project_id="proj-001",
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
        updated_at="2026-01-01T00:00:00Z",
    )


def _make_subs_repo() -> MagicMock:
    return MagicMock()


def _make_vapid_repo() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# dispatch — should_notify=False → send_to_all NOT called
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_no_push_when_toggle_disabled() -> None:
    """dispatch() with toggle disabled → web_push.send_to_all is NOT called."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=False)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock()
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    send_to_all_mock.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch — should_notify=True → send_to_all IS called (via task)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_calls_send_to_all_when_toggle_enabled() -> None:
    """dispatch() with toggle enabled → web_push.send_to_all IS called (via task)."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock(return_value=[])
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)  # drain the scheduled task

    send_to_all_mock.assert_called_once()


@pytest.mark.anyio
async def test_dispatch_passes_correct_title_to_send_to_all() -> None:
    """dispatch() passes title = project.domain/project.name to send_to_all."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock(return_value=[])
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    call_kwargs = send_to_all_mock.call_args
    assert call_kwargs is not None
    title = call_kwargs.kwargs.get("title") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs.get("title")
    assert title == "sandbox/myproject"


@pytest.mark.anyio
async def test_dispatch_passes_repos_to_send_to_all() -> None:
    """dispatch() passes subscriptions_repo and vapid_repo to send_to_all."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock(return_value=[])
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    call_kwargs = send_to_all_mock.call_args
    assert call_kwargs is not None
    # First two positional args should be the repos
    args = call_kwargs.args
    assert len(args) >= 2
    assert args[0] is subs_repo
    assert args[1] is vapid_repo


@pytest.mark.anyio
async def test_dispatch_passes_data_url_with_project_id() -> None:
    """dispatch() passes data.url = /projects/{project.id} to send_to_all."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock(return_value=[])
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    call_kwargs = send_to_all_mock.call_args
    assert call_kwargs is not None
    data = call_kwargs.kwargs.get("data")
    assert data is not None
    assert data["url"] == f"/projects/{project.id}"
    assert data["event_type"] == event.event_type


# ---------------------------------------------------------------------------
# dispatch — send_to_all raises → dispatch swallows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_swallows_send_to_all_exception() -> None:
    """When send_to_all raises, dispatch returns None without propagating."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock(side_effect=RuntimeError("push exploded"))
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        result = await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    assert result is None


# ---------------------------------------------------------------------------
# dispatch — unknown event type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_unknown_event_type_returns_none() -> None:
    """dispatch() with unknown event_type → None (should_notify=False → no task)."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("UnknownEventType")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    send_to_all_mock = AsyncMock()
    with patch("claude_remote.services.web_push.send_to_all", send_to_all_mock):
        result = await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )
        await asyncio.sleep(0)

    assert result is None
    send_to_all_mock.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch — never raises on internal error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_never_raises_on_internal_error() -> None:
    """dispatch() must return None even if should_notify itself raises."""
    from claude_remote.services import notifier

    prefs = _make_prefs(notify_on_notification=True)
    event = _make_event("Notification")
    project = _make_project()
    subs_repo = _make_subs_repo()
    vapid_repo = _make_vapid_repo()

    with patch.object(notifier, "should_notify", side_effect=RuntimeError("boom")):
        result = await notifier.dispatch(
            event, project, prefs,
            subscriptions_repo=subs_repo,
            vapid_repo=vapid_repo,
        )

    assert result is None
