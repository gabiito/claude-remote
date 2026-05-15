"""Red tests for extract_snippet — WU-2.

13 cases covering all REQ-2 scenarios.
Tests FAIL until services/event_snippet.py is implemented.
"""

from __future__ import annotations

from claude_remote.db.events import Event
from claude_remote.services.event_snippet import extract_snippet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str, payload: str) -> Event:
    return Event(
        id="event-1",
        instance_id="inst-1",
        project_id="proj-1",
        event_type=event_type,
        payload=payload,
        received_at="2026-05-14T22:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_notification_normal_message() -> None:
    """Notification with short message → returned as-is."""
    ev = _make_event("Notification", '{"message": "Need approval"}')
    assert extract_snippet(ev) == "Need approval"


def test_notification_long_message_truncated() -> None:
    """Notification with message > 80 chars → truncated with …"""
    long_msg = "A" * 100
    ev = _make_event("Notification", f'{{"message": "{long_msg}"}}')
    result = extract_snippet(ev)
    assert len(result) == 80  # 79 chars + ellipsis character
    assert result.endswith("…")
    assert result.startswith("A")


def test_notification_empty_payload() -> None:
    """Notification with empty dict payload → empty string."""
    ev = _make_event("Notification", "{}")
    assert extract_snippet(ev) == ""


def test_pretooluse_with_tool_name() -> None:
    """PreToolUse with tool_name → tool_name string."""
    ev = _make_event("PreToolUse", '{"tool_name": "Edit", "tool_input": {}}')
    assert extract_snippet(ev) == "Edit"


def test_posttooluse_fallback_tool_key() -> None:
    """PostToolUse with 'tool' key (fallback) → tool string."""
    ev = _make_event("PostToolUse", '{"tool": "Read"}')
    assert extract_snippet(ev) == "Read"


def test_pretooluse_no_tool_keys() -> None:
    """PreToolUse with no tool_name or tool key → empty string."""
    ev = _make_event("PreToolUse", '{"other": "value"}')
    assert extract_snippet(ev) == ""


def test_invalid_json_payload() -> None:
    """Invalid JSON payload → empty string (no exception raised)."""
    ev = _make_event("Notification", "{not json")
    assert extract_snippet(ev) == ""


def test_json_array_payload() -> None:
    """JSON array payload → empty string (dict guard)."""
    ev = _make_event("Notification", "[1, 2, 3]")
    assert extract_snippet(ev) == ""


def test_notification_message_null() -> None:
    """Notification with message=null → empty string."""
    ev = _make_event("Notification", '{"message": null}')
    assert extract_snippet(ev) == ""


def test_notification_message_non_string() -> None:
    """Notification with message=123 (non-string) → empty string."""
    ev = _make_event("Notification", '{"message": 123}')
    assert extract_snippet(ev) == ""


def test_session_start_no_snippet() -> None:
    """SessionStart → always empty."""
    ev = _make_event("SessionStart", "{}")
    assert extract_snippet(ev) == ""


def test_stop_no_snippet() -> None:
    """Stop → always empty."""
    ev = _make_event("Stop", '{"reason": "user"}')
    assert extract_snippet(ev) == ""


def test_session_end_no_snippet() -> None:
    """SessionEnd → always empty."""
    ev = _make_event("SessionEnd", '{"exit_code": 0}')
    assert extract_snippet(ev) == ""


# ---------------------------------------------------------------------------
# Filter wiring test (introspection)
# ---------------------------------------------------------------------------


def test_jinja2_filters_registered() -> None:
    """Both format_relative and extract_snippet are registered as Jinja2 filters."""
    from claude_remote.routes._templates import templates

    assert "format_relative" in templates.env.filters
    assert "extract_snippet" in templates.env.filters
