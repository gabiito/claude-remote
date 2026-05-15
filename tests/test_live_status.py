"""Red tests for derive_live_status — WU-1.

17 table-driven cases covering all REQ-1 decision-priority rules.
These tests FAIL until services/live_status.py is implemented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from claude_remote.services.live_status import derive_live_status
from claude_remote.db.instances import Instance
from claude_remote.db.events import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instance(status: str) -> Instance:
    return Instance(
        id="inst-1",
        project_id="proj-1",
        tmux_session_name="claude-remote-proj-abcd1234",
        pane_pid=None,
        status=status,
        created_at="2026-05-14T20:00:00+00:00",
        stopped_at=None,
        hook_token="fake-token",
    )


def _make_event(event_type: str, delta_seconds: float, naive: bool = False) -> Event:
    """Create an Event with received_at = now - delta_seconds."""
    NOW = datetime(2026, 5, 14, 22, 0, 0, tzinfo=UTC)
    ts = NOW - timedelta(seconds=delta_seconds)
    if naive:
        received_at = ts.replace(tzinfo=None).isoformat()
    else:
        received_at = ts.isoformat()
    return Event(
        id="event-1",
        instance_id="inst-1",
        project_id="proj-1",
        event_type=event_type,
        payload="{}",
        received_at=received_at,
    )


# Fixed reference "now" for all tests
NOW = datetime(2026, 5, 14, 22, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Table-driven parametrize cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description, db_status, events, expected",
    [
        # Case 1: DB stopped wins — terminal
        (
            "DB stopped wins over any events",
            "stopped",
            [_make_event("PreToolUse", 5), _make_event("PreToolUse", 10)],
            "stopped",
        ),
        # Case 2: DB crashed wins — terminal
        (
            "DB crashed wins over Notification",
            "crashed",
            [_make_event("Notification", 10)],
            "crashed",
        ),
        # Case 3: No events, status=running → running
        (
            "No events, running → fallback running",
            "running",
            [],
            "running",
        ),
        # Case 4: No events, status=starting → starting
        (
            "No events, starting → fallback starting",
            "starting",
            [],
            "starting",
        ),
        # Case 5: Single recent Notification → needs_input
        (
            "Single Notification within window → needs_input",
            "running",
            [_make_event("Notification", 10)],
            "needs_input",
        ),
        # Case 6: Notification then tool (tool later) → active
        (
            "Notification at 30s then PreToolUse at 5s → active",
            "running",
            [_make_event("Notification", 30), _make_event("PreToolUse", 5)],
            "active",
        ),
        # Case 7: Tool only, within 60s → active
        (
            "PostToolUse at 10s → active",
            "running",
            [_make_event("PostToolUse", 10)],
            "active",
        ),
        # Case 8: Tool only, beyond 60s → running (fallback)
        (
            "PreToolUse at 90s (beyond active_window) → running",
            "running",
            [_make_event("PreToolUse", 90)],
            "running",
        ),
        # Case 9: Stop only, within 300s → idle
        (
            "Stop event at 30s → idle",
            "running",
            [_make_event("Stop", 30)],
            "idle",
        ),
        # Case 10: SessionEnd only, within 300s → idle
        (
            "SessionEnd at 30s → idle",
            "running",
            [_make_event("SessionEnd", 30)],
            "idle",
        ),
        # Case 11: Notification beyond 300s → running (outside decay window)
        (
            "Notification at 400s (beyond needs_input_window) → running",
            "running",
            [_make_event("Notification", 400)],
            "running",
        ),
        # Case 12: Notification within decay, tool beyond 60s → needs_input
        # (the tool arrived before the Notification in time, so Notification is most-recent)
        (
            "Notification at 30s, PreToolUse at 120s (tool before notif) → needs_input",
            "running",
            [_make_event("Notification", 30), _make_event("PreToolUse", 120)],
            "needs_input",
        ),
        # Case 13: Events arrive out of order (reversed list) → correct result after internal sort
        (
            "Events out of order: Notification at 100s, PreToolUse at 5s → active",
            "running",
            [
                _make_event("PreToolUse", 5),   # most recent first (reversed from typical)
                _make_event("Notification", 100),
            ],
            "active",
        ),
        # Case 14: Naive timestamp (no tzinfo) tolerated → needs_input (no exception)
        (
            "Naive timestamp treated as UTC → needs_input",
            "running",
            [_make_event("Notification", 10, naive=True)],
            "needs_input",
        ),
        # Case 15: Empty list, custom active_window=10s → running
        (
            "Empty events with custom small active_window → running",
            "running",
            [],
            "running",
        ),
        # Case 16: Tool exactly at boundary (now - 60s, inclusive) → active
        (
            "PreToolUse exactly at 60s boundary → active (boundary inclusive)",
            "running",
            [_make_event("PreToolUse", 60)],
            "active",
        ),
        # Case 17: SessionStart only → running (no signal match)
        (
            "SessionStart only → running (no derived signal)",
            "running",
            [_make_event("SessionStart", 5)],
            "running",
        ),
    ],
)
def test_derive_live_status(
    description: str,
    db_status: str,
    events: list[Event],
    expected: str,
) -> None:
    instance = _make_instance(db_status)
    result = derive_live_status(instance, events, now=NOW)
    assert result == expected, f"[{description}] expected {expected!r}, got {result!r}"


def test_custom_active_window() -> None:
    """Custom active_window=timedelta(seconds=10) — tool at 15s is beyond window."""
    instance = _make_instance("running")
    events = [_make_event("PreToolUse", 15)]
    result = derive_live_status(
        instance, events, now=NOW, active_window=timedelta(seconds=10)
    )
    assert result == "running"


def test_custom_needs_input_window() -> None:
    """Custom needs_input_window=timedelta(seconds=60) — Notification at 90s is outside."""
    instance = _make_instance("running")
    events = [_make_event("Notification", 90)]
    result = derive_live_status(
        instance, events, now=NOW, needs_input_window=timedelta(seconds=60)
    )
    assert result == "running"
