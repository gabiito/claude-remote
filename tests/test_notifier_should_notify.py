"""Red tests for WU-2 — notifier.should_notify pure function.

Tests run BEFORE the implementation exists. All must fail until green commit lands.

Covers:
  - 6 event types × toggle enabled/disabled
  - Unknown event type → False
  - quiet_hours None → no suppression
  - Only one of start/end set → fail open
  - Normal same-day range (start < end): inside/outside/boundary
  - Wrap-around (start > end, overnight): inside/outside/boundary
  - start == end → no quiet hours
  - Malformed quiet_hours_start → fail open
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from claude_remote.db.events import Event
from claude_remote.db.notifications import NotificationPreferences


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str, payload: str = "{}") -> Event:
    return Event(
        id="test-id",
        instance_id=None,
        project_id=None,
        event_type=event_type,
        payload=payload,
        received_at="2026-01-01T00:00:00+00:00",
    )


def _make_prefs(
    *,
    notify_on_notification: bool = True,
    notify_on_stop: bool = True,
    notify_on_session_end: bool = True,
    notify_on_session_start: bool = True,
    notify_on_pre_tool_use: bool = True,
    notify_on_post_tool_use: bool = True,
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
    ntfy_topic: str = "test-topic",
) -> NotificationPreferences:
    return NotificationPreferences(
        notify_on_notification=notify_on_notification,
        notify_on_stop=notify_on_stop,
        notify_on_session_end=notify_on_session_end,
        notify_on_session_start=notify_on_session_start,
        notify_on_pre_tool_use=notify_on_pre_tool_use,
        notify_on_post_tool_use=notify_on_post_tool_use,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        ntfy_topic=ntfy_topic,
        updated_at="2026-01-01T00:00:00Z",
    )


def _now_at(hour: int, minute: int = 0) -> datetime:
    """Return a UTC-aware datetime; the notifier uses .astimezone() for local time.
    We force the server to use UTC in tests via timezone-aware datetimes.
    The function under test calls now.astimezone().time() — for UTC server
    (TZ=UTC), this equals the UTC time directly.
    """
    # Create a datetime in UTC; .astimezone() without arg uses local tz.
    # Tests use monkeypatched or explicit datetime; this helper returns UTC-aware.
    return datetime(2026, 1, 15, hour, minute, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------


def _get_should_notify():
    from claude_remote.services.notifier import should_notify  # type: ignore[import]

    return should_notify


# ---------------------------------------------------------------------------
# Event-type toggle tests (6 × enabled + 6 × disabled = 12)
# ---------------------------------------------------------------------------


class TestEventTypeToggle:
    @pytest.mark.parametrize(
        "event_type,toggle_field",
        [
            ("Notification", "notify_on_notification"),
            ("Stop", "notify_on_stop"),
            ("SessionEnd", "notify_on_session_end"),
            ("SessionStart", "notify_on_session_start"),
            ("PreToolUse", "notify_on_pre_tool_use"),
            ("PostToolUse", "notify_on_post_tool_use"),
        ],
    )
    def test_toggle_enabled_no_quiet_hours_returns_true(
        self, event_type: str, toggle_field: str
    ) -> None:
        """Toggle ON + no quiet hours → True for all 6 event types."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(**{toggle_field: True})
        event = _make_event(event_type)
        assert should_notify(event, prefs, now=_now_at(12)) is True

    @pytest.mark.parametrize(
        "event_type,toggle_field",
        [
            ("Notification", "notify_on_notification"),
            ("Stop", "notify_on_stop"),
            ("SessionEnd", "notify_on_session_end"),
            ("SessionStart", "notify_on_session_start"),
            ("PreToolUse", "notify_on_pre_tool_use"),
            ("PostToolUse", "notify_on_post_tool_use"),
        ],
    )
    def test_toggle_disabled_returns_false(
        self, event_type: str, toggle_field: str
    ) -> None:
        """Toggle OFF → False regardless of quiet hours."""
        should_notify = _get_should_notify()
        # All toggles ON except the one under test
        kwargs = {
            "notify_on_notification": True,
            "notify_on_stop": True,
            "notify_on_session_end": True,
            "notify_on_session_start": True,
            "notify_on_pre_tool_use": True,
            "notify_on_post_tool_use": True,
        }
        kwargs[toggle_field] = False
        prefs = _make_prefs(**kwargs)  # type: ignore[arg-type]
        event = _make_event(event_type)
        assert should_notify(event, prefs, now=_now_at(12)) is False

    def test_unknown_event_type_returns_false(self) -> None:
        """Unknown event_type → False (no matching toggle)."""
        should_notify = _get_should_notify()
        prefs = _make_prefs()
        event = _make_event("UnknownType")
        assert should_notify(event, prefs, now=_now_at(12)) is False


# ---------------------------------------------------------------------------
# Quiet hours — None (no suppression)
# ---------------------------------------------------------------------------


class TestQuietHoursNone:
    def test_both_none_no_suppression(self) -> None:
        """quiet_hours_start=None and quiet_hours_end=None → no suppression."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start=None, quiet_hours_end=None)
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(3)) is True

    def test_only_start_set_ignores_quiet_hours(self) -> None:
        """Only quiet_hours_start set (end=None) → fail open, no suppression."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start="22:00", quiet_hours_end=None)
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(23)) is True

    def test_only_end_set_ignores_quiet_hours(self) -> None:
        """Only quiet_hours_end set (start=None) → fail open, no suppression."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start=None, quiet_hours_end="08:00")
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(5)) is True


# ---------------------------------------------------------------------------
# Normal (same-day) quiet hours range: start < end
# e.g. 09:00–17:00 (work hours)
# ---------------------------------------------------------------------------


class TestNormalRangeQuietHours:
    """Tests with quiet_hours_start=09:00 and quiet_hours_end=17:00 (non-wrapping)."""

    def _prefs(self) -> NotificationPreferences:
        return _make_prefs(quiet_hours_start="09:00", quiet_hours_end="17:00")

    def test_now_inside_range_returns_false(self) -> None:
        """now=12:00 is inside [09:00, 17:00) → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(12)) is False

    def test_now_before_range_returns_true(self) -> None:
        """now=08:00 is before 09:00 → do not suppress (True)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(8)) is True

    def test_now_after_range_returns_true(self) -> None:
        """now=18:00 is after 17:00 → do not suppress (True)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(18)) is True

    def test_exactly_at_start_boundary_returns_false(self) -> None:
        """now=09:00 exactly (inclusive start) → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(9)) is False

    def test_exactly_at_end_boundary_returns_true(self) -> None:
        """now=17:00 exactly (exclusive end per spec) → do not suppress (True)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(17)) is True


# ---------------------------------------------------------------------------
# Wrap-around (overnight) quiet hours: start > end
# e.g. 23:00–07:00 (night hours)
# ---------------------------------------------------------------------------


class TestWrapAroundQuietHours:
    """Tests with quiet_hours_start=23:00 and quiet_hours_end=07:00 (overnight)."""

    def _prefs(self) -> NotificationPreferences:
        return _make_prefs(quiet_hours_start="23:00", quiet_hours_end="07:00")

    def test_now_inside_range_after_midnight(self) -> None:
        """now=01:00 is inside overnight window → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(1)) is False

    def test_now_inside_range_before_midnight(self) -> None:
        """now=23:30 is inside overnight window → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(23, 30)) is False

    def test_now_outside_range_midday(self) -> None:
        """now=12:00 is outside overnight window → do not suppress (True)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(12)) is True

    def test_exactly_at_start_boundary_returns_false(self) -> None:
        """now=23:00 exactly (inclusive start) → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(23)) is False

    def test_exactly_at_end_boundary_returns_true(self) -> None:
        """now=07:00 exactly (exclusive end) → do not suppress (True)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(7)) is True

    def test_just_before_end_boundary_returns_false(self) -> None:
        """now=06:59 is inside overnight window → suppress (False)."""
        should_notify = _get_should_notify()
        assert should_notify(_make_event("Notification"), self._prefs(), now=_now_at(6, 59)) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_start_equals_end_no_quiet_hours(self) -> None:
        """start == end → zero-duration window → no quiet hours, returns True."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start="12:00", quiet_hours_end="12:00")
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(12)) is True

    def test_malformed_start_fails_open(self) -> None:
        """Malformed quiet_hours_start → fail open (True), no raise."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start="25:99", quiet_hours_end="08:00")
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(2)) is True

    def test_malformed_end_fails_open(self) -> None:
        """Malformed quiet_hours_end → fail open (True), no raise."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start="22:00", quiet_hours_end="not-a-time")
        event = _make_event("Notification")
        assert should_notify(event, prefs, now=_now_at(23)) is True

    def test_should_notify_never_raises(self) -> None:
        """should_notify must not raise even with garbage inputs."""
        should_notify = _get_should_notify()
        prefs = _make_prefs(quiet_hours_start="GARBAGE", quiet_hours_end="MORE_GARBAGE")
        event = _make_event("Notification")
        result = should_notify(event, prefs, now=_now_at(12))
        assert isinstance(result, bool)
