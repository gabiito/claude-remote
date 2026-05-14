"""Claude Code settings.json service.

Provides apply_hooks_to_settings() — an idempotent merge function that writes
Claude Code hook configuration for all 6 event types into a project's
.claude/settings.json file.

Hook format (Claude Code hooks schema):
  settings["hooks"][event_type] = [
      {
          "matcher": "*",
          "hooks": [{"type": "command", "command": "curl ... -d @-"}]
      }
  ]

The curl command receives the event payload on stdin (-d @-) and POSTs it
to our /hooks/{event_type}?token={hook_token} endpoint.

Merge policy (locked Q4):
  - Preserve ALL non-hooks top-level keys.
  - For each of our 6 event types, replace/add the hook entry unconditionally
    (our hook wins over any user-defined hook for the same event type).
  - Other event types that the user may have defined are left unchanged.
    NOTE: this means unknown-to-us event types in the user's hooks section are
    preserved; only the 6 types we own are overwritten.

Error policy:
  - Malformed JSON in an existing file: raise ValueError (do NOT silently
    overwrite). Caller (launcher) decides whether to abort or recover.
  - Parent directory creation is handled transparently (mkdir -p).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVENT_TYPES: tuple[str, ...] = (
    "SessionStart",
    "Notification",
    "Stop",
    "PreToolUse",
    "PostToolUse",
    "SessionEnd",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_hooks_to_settings(
    settings_path: Path,
    hook_token: str,
    base_url: str,
) -> None:
    """Merge hook entries for all 6 event types into the given settings.json.

    Args:
        settings_path: absolute path to the project's .claude/settings.json.
        hook_token: per-instance bearer token for /hooks/* authentication.
        base_url: URL of the claude-remote server (trailing slash stripped).
            Example: "http://localhost:8000" or "http://100.64.0.1:8000"

    Raises:
        ValueError: if settings_path exists but contains malformed JSON.
            The file is NOT modified in this case.
        OSError: if the parent directory cannot be created (permissions, etc.).
    """
    # Strip trailing slash once — used in all URL constructions
    base = base_url.rstrip("/")

    # Ensure parent directory exists
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings or start fresh
    if settings_path.exists():
        raw = settings_path.read_text()
        try:
            settings: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"settings.json at {settings_path} contains malformed JSON: {exc}"
            ) from exc
    else:
        settings: dict[str, Any] = {}

    # Ensure hooks section exists (preserve other content)
    if "hooks" not in settings:
        settings["hooks"] = {}

    # Write our hook entry for each event type (our hooks always win)
    for event_type in EVENT_TYPES:
        command = (
            f"curl -s -X POST '{base}/hooks/{event_type}?token={hook_token}'"
            f" -H 'Content-Type: application/json' -d @-"
        )
        settings["hooks"][event_type] = [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": command}],
            }
        ]

    # Write back with consistent formatting + trailing newline
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    logger.debug("Wrote hooks settings to %s", settings_path)
