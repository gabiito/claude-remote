"""Red tests for WU-4 — apply_hooks_to_settings merge cases.

The function must:
  1. Create parent directories if they don't exist.
  2. Write hooks for all 6 event types.
  3. Merge: preserve unrelated keys, only replace hooks section.
  4. Raise ValueError (not silently overwrite) on malformed JSON.
  5. Strip trailing slash from base_url before constructing URLs.

Claude Code hooks format:
  settings["hooks"][event_type] = [
      {"matcher": "*", "hooks": [{"type": "command", "command": "<curl cmd>"}]}
  ]
"""

import json
from pathlib import Path

import pytest

from claude_remote.services.claude_settings import EVENT_TYPES, apply_hooks_to_settings

BASE_URL = "http://localhost:8000"
TOKEN = "test-hook-token-abc123"
EXPECTED_URL_PREFIX = f"{BASE_URL}/hooks/"


def _expected_command(event_type: str) -> str:
    return (
        f"curl -s -X POST '{BASE_URL}/hooks/{event_type}?token={TOKEN}'"
        f" -H 'Content-Type: application/json' -d @-"
    )


# ---------------------------------------------------------------------------
# Case 1: No file — create parent dir + write fresh
# ---------------------------------------------------------------------------


def test_no_file_creates_parent_dir(tmp_path: Path) -> None:
    """Function creates the parent directory (.claude/) when it doesn't exist."""
    settings_path = tmp_path / "nonexistent" / "nested" / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    assert settings_path.parent.exists()


def test_no_file_creates_settings_json(tmp_path: Path) -> None:
    """Function creates settings.json when it doesn't exist."""
    settings_path = tmp_path / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    assert settings_path.exists()


def test_no_file_writes_all_six_event_types(tmp_path: Path) -> None:
    """New file must contain all 6 event types in hooks section."""
    settings_path = tmp_path / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())
    assert "hooks" in data
    for ev in EVENT_TYPES:
        assert ev in data["hooks"], f"Missing event type: {ev}"


def test_no_file_correct_hook_command_format(tmp_path: Path) -> None:
    """Hook entries must use Claude Code's command format with curl."""
    settings_path = tmp_path / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())
    for ev in EVENT_TYPES:
        hook_entry = data["hooks"][ev]
        # Must be a list of hook groups
        assert isinstance(hook_entry, list), f"hooks[{ev}] must be a list"
        assert len(hook_entry) > 0
        # First group must have hooks list with command entry
        assert "hooks" in hook_entry[0]
        cmd_entry = hook_entry[0]["hooks"][0]
        assert cmd_entry["type"] == "command"
        assert ev in cmd_entry["command"]
        assert TOKEN in cmd_entry["command"]


# ---------------------------------------------------------------------------
# Case 2: File with unrelated keys — keys preserved, hooks section updated
# ---------------------------------------------------------------------------


def test_preserves_unrelated_keys(tmp_path: Path) -> None:
    """Existing non-hooks keys must be preserved."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"allowedTools": ["Read"], "theme": "dark"}))

    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())
    assert data["allowedTools"] == ["Read"]
    assert data["theme"] == "dark"
    assert "hooks" in data


def test_preserves_unrelated_keys_and_adds_all_event_types(tmp_path: Path) -> None:
    """Preserves keys AND adds all 6 event types."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"allowedTools": ["Read"]}))

    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())
    for ev in EVENT_TYPES:
        assert ev in data["hooks"]


# ---------------------------------------------------------------------------
# Case 3: File with conflicting hook entries — our hooks win
# ---------------------------------------------------------------------------


def test_our_hooks_replace_conflicting_entries(tmp_path: Path) -> None:
    """Our hook entry replaces user's existing hook for the same event type."""
    existing = {
        "hooks": {
            "Notification": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "user-script.sh"}],
                }
            ]
        }
    }
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(existing))

    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())

    # Our Notification hook must be there (not the user's)
    notification_hooks = data["hooks"]["Notification"]
    cmd = notification_hooks[0]["hooks"][0]["command"]
    assert TOKEN in cmd, "Our hook must replace the user's hook"
    assert "user-script.sh" not in cmd, "User's hook must be replaced"

    # All 6 event types must be present
    for ev in EVENT_TYPES:
        assert ev in data["hooks"]


# ---------------------------------------------------------------------------
# Case 4: Malformed JSON — raise ValueError, do NOT overwrite
# ---------------------------------------------------------------------------


def test_malformed_json_raises_value_error(tmp_path: Path) -> None:
    """Malformed JSON file must raise ValueError."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")

    with pytest.raises(ValueError):
        apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)


def test_malformed_json_does_not_overwrite_file(tmp_path: Path) -> None:
    """Malformed JSON file must NOT be overwritten when ValueError is raised."""
    original_content = "{not valid json"
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(original_content)

    try:
        apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    except ValueError:
        pass

    assert settings_path.read_text() == original_content, "File must not be overwritten on error"


# ---------------------------------------------------------------------------
# Case 5: base_url with trailing slash — no double slash in URL
# ---------------------------------------------------------------------------


def test_trailing_slash_stripped_from_base_url(tmp_path: Path) -> None:
    """Trailing slash on base_url must not produce double-slash URLs."""
    settings_path = tmp_path / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, "http://localhost:8000/")
    data = json.loads(settings_path.read_text())
    for ev in EVENT_TYPES:
        cmd = data["hooks"][ev][0]["hooks"][0]["command"]
        assert "//" not in cmd.split("POST '", 1)[-1], f"Double slash in URL for {ev}: {cmd}"


# ---------------------------------------------------------------------------
# Case 6: Empty file (valid JSON — empty object)
# ---------------------------------------------------------------------------


def test_empty_json_object_writes_hooks(tmp_path: Path) -> None:
    """File with only '{}' must be treated as valid empty settings."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}")

    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    data = json.loads(settings_path.read_text())
    assert "hooks" in data
    for ev in EVENT_TYPES:
        assert ev in data["hooks"]


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


def test_output_is_valid_json_with_trailing_newline(tmp_path: Path) -> None:
    """Written file must be valid JSON and end with a newline."""
    settings_path = tmp_path / ".claude" / "settings.json"
    apply_hooks_to_settings(settings_path, TOKEN, BASE_URL)
    raw = settings_path.read_text()
    assert raw.endswith("\n")
    json.loads(raw)  # must not raise
