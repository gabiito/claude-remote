"""Extract a short display snippet from an event payload.

Rules (REQ-2):
  - Notification      → payload["message"][:max_length] (truncated with "…")
  - PreToolUse        → payload.get("tool_name") or payload.get("tool") fallback
  - PostToolUse       → same as PreToolUse
  - SessionStart      → "" (no snippet)
  - Stop              → "" (no snippet)
  - SessionEnd        → "" (no snippet)
  - JSON parse error  → "" (warn-logged, NEVER raises)
  - Non-dict payload  → "" (dict guard)
  - Non-string value  → "" (type guard)

The function MUST NOT raise under any input.
"""

from __future__ import annotations

import json
import logging

from claude_remote.db.events import Event

_log = logging.getLogger(__name__)

_DEFAULT_MAX = 80


def extract_snippet(event: Event, max_length: int = _DEFAULT_MAX) -> str:
    """Return a short, display-safe text snippet extracted from ``event.payload``.

    Args:
        event: the Event record (payload is a raw JSON string).
        max_length: maximum character length before truncation with ``…``.
            Defaults to 80.

    Returns:
        A string of at most ``max_length`` characters, or ``""`` if no
        applicable snippet exists or any error occurs.
    """
    try:
        payload = json.loads(event.payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        _log.warning("event %s payload not JSON; snippet skipped", event.id)
        return ""

    if not isinstance(payload, dict):
        return ""

    et = event.event_type

    if et == "Notification":
        msg = payload.get("message", "") or ""
        if not isinstance(msg, str):
            return ""
        if len(msg) > max_length:
            return msg[: max_length - 1] + "…"
        return msg

    if et in ("PreToolUse", "PostToolUse"):
        name = payload.get("tool_name") or payload.get("tool") or ""
        return name if isinstance(name, str) else ""

    # SessionStart, Stop, SessionEnd — no snippet.
    return ""
