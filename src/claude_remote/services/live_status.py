"""Derive a live display status from DB instance state and recent events.

The function is PURE: no I/O, no global state, no clock reads.
Callers MUST inject ``now`` (UTC-aware datetime) for deterministic tests.

Decision priority (REQ-1):
  1. DB status ∈ {stopped, crashed} → return it (terminal wins, no override).
  2. Most-recent Notification within needs_input_window AND no tool event
     strictly after that Notification → return ``needs_input``.
  3. Most-recent tool event (PreToolUse / PostToolUse) within active_window
     → return ``active``.
  4. Most-recent terminal-ish event (Stop / SessionEnd) within
     needs_input_window → return ``idle``.
  5. Fallback → return ``instance.status`` (running / starting).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from claude_remote.db.events import Event
from claude_remote.db.instances import Instance

# ---------------------------------------------------------------------------
# Type alias and constant sets
# ---------------------------------------------------------------------------

LiveStatus = Literal[
    "running",
    "active",
    "needs_input",
    "idle",
    "stopped",
    "crashed",
    "starting",
]

TERMINAL_STATUSES: frozenset[str] = frozenset({"stopped", "crashed"})
TOOL_EVENT_TYPES: frozenset[str] = frozenset({"PreToolUse", "PostToolUse"})
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"Stop", "SessionEnd"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_aware(iso: str) -> datetime:
    """Parse an ISO 8601 string, ensuring the result is UTC-aware.

    Handles:
    - ``2026-05-14T21:00:00Z``         — Python 3.11+ fromisoformat parses Z
    - ``2026-05-14T21:00:00+00:00``    — standard offset notation
    - ``2026-05-14T21:00:00``          — naive: assumed UTC
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def derive_live_status(
    instance: Instance,
    recent_events: list[Event],
    *,
    now: datetime,
    active_window: timedelta = timedelta(seconds=60),
    needs_input_window: timedelta = timedelta(seconds=300),
) -> LiveStatus:
    """Return the display status for an instance based on DB state + recent events.

    Args:
        instance: the DB instance record (status must be one of the 4 DB values).
        recent_events: up to 20 most-recent events for this instance.
            Ordering is not trusted — the function sorts internally (ADR-9).
        now: UTC-aware reference datetime injected by caller.
        active_window: recency threshold for tool events → ``active``.
        needs_input_window: recency threshold for Notification/idle events.

    Returns:
        One of ``running | active | needs_input | idle | stopped | crashed | starting``.
    """
    # Rule 1: terminal DB statuses are authoritative.
    if instance.status in TERMINAL_STATUSES:
        return instance.status  # type: ignore[return-value]

    # Defensive sort: most recent first, regardless of caller ordering (ADR-9).
    parsed: list[tuple[datetime, Event]] = sorted(
        ((_parse_aware(e.received_at), e) for e in recent_events),
        key=lambda pair: pair[0],
        reverse=True,
    )

    # Single scan: collect most-recent match per category.
    most_recent_tool: tuple[datetime, Event] | None = None
    most_recent_notification: tuple[datetime, Event] | None = None
    most_recent_terminal: tuple[datetime, Event] | None = None

    for ts, ev in parsed:
        age = now - ts

        if ev.event_type in TOOL_EVENT_TYPES and age <= active_window:
            if most_recent_tool is None:
                most_recent_tool = (ts, ev)

        if ev.event_type == "Notification" and age <= needs_input_window:
            if most_recent_notification is None:
                most_recent_notification = (ts, ev)

        if ev.event_type in TERMINAL_EVENT_TYPES and age <= needs_input_window:
            if most_recent_terminal is None:
                most_recent_terminal = (ts, ev)

    # Rule 2: Notification not superseded by a more-recent tool event.
    if most_recent_notification is not None:
        notif_ts = most_recent_notification[0]
        # Tool supersedes only if it arrived STRICTLY AFTER the Notification.
        if most_recent_tool is None or most_recent_tool[0] < notif_ts:
            return "needs_input"

    # Rule 3: Active tool work within active_window.
    if most_recent_tool is not None:
        return "active"

    # Rule 4: Recent Stop / SessionEnd → idle.
    if most_recent_terminal is not None:
        return "idle"

    # Rule 5: Fallback to DB status (running / starting).
    return instance.status  # type: ignore[return-value]
