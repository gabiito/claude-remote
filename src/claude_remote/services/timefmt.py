"""Format an ISO 8601 timestamp as a Spanish relative time string.

Rules (REQ-3):
  | delta < 5s       → "ahora"
  | delta < 60s      → "hace Ns"
  | delta < 3600s    → "hace Nm"
  | delta < 86400s   → "hace Nh"
  | delta >= 86400s  → "hace Nd"

Negative deltas (clock drift / future timestamps) → "ahora".
Unparseable input → "" (NEVER raises).
"""

from __future__ import annotations

from datetime import UTC, datetime


def format_relative(
    timestamp: str | datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    """Format a timestamp as a Spanish relative time string.

    Args:
        timestamp: an ISO 8601 UTC string or a ``datetime`` object.
            ``None`` and empty strings return ``""``.
        now: reference datetime (UTC-aware). Defaults to ``datetime.now(UTC)``.

    Returns:
        A human-friendly Spanish string like ``"hace 5s"``, ``"hace 3m"``, etc.
        Returns ``""`` on any parse error. NEVER raises.
    """
    if now is None:
        now = datetime.now(UTC)

    if timestamp is None:
        return ""

    try:
        if isinstance(timestamp, datetime):
            ts = timestamp
        else:
            if not timestamp:
                return ""
            ts = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return ""

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    delta_s = int((now - ts).total_seconds())

    if delta_s < 5:
        return "ahora"
    if delta_s < 60:
        return f"hace {delta_s}s"
    if delta_s < 3600:
        return f"hace {delta_s // 60}m"
    if delta_s < 86400:
        return f"hace {delta_s // 3600}h"
    return f"hace {delta_s // 86400}d"
