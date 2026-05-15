"""Red tests for format_relative — WU-2.

15+ cases covering all boundary rows from design §4.3 plus error/edge cases.
Tests FAIL until services/timefmt.py is implemented.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claude_remote.services.timefmt import format_relative

# Fixed reference "now" for all parametrized tests
NOW = datetime(2026, 5, 14, 22, 0, 0, tzinfo=UTC)


def _ts(delta_seconds: float) -> str:
    """Return ISO 8601 string for NOW - delta_seconds."""
    from datetime import timedelta
    return (NOW - timedelta(seconds=delta_seconds)).isoformat()


# ---------------------------------------------------------------------------
# Boundary table (all rows from design §4.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta_seconds, expected",
    [
        (-10, "ahora"),     # future/negative delta → ahora
        (0, "ahora"),       # no delta → ahora
        (4, "ahora"),       # < 5s → ahora
        (5, "hace 5s"),     # first second boundary
        (59, "hace 59s"),   # just below 60s
        (60, "hace 1m"),    # minute boundary (60 belongs to minutes)
        (3599, "hace 59m"), # just below 3600
        (3600, "hace 1h"),  # hour boundary
        (86399, "hace 23h"), # just below 86400
        (86400, "hace 1d"),  # day boundary
        (172800, "hace 2d"), # 2 days
    ],
)
def test_format_relative_boundaries(delta_seconds: float, expected: str) -> None:
    ts = _ts(delta_seconds)
    result = format_relative(ts, now=NOW)
    assert result == expected, f"Δ={delta_seconds}s: expected {expected!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------


def test_format_relative_empty_string() -> None:
    """Empty string input → empty string output."""
    assert format_relative("", now=NOW) == ""


def test_format_relative_not_a_date() -> None:
    """Non-parseable string → empty string (no exception)."""
    assert format_relative("not-a-date", now=NOW) == ""


def test_format_relative_none_input() -> None:
    """None input → empty string (TypeError guard, no exception)."""
    assert format_relative(None, now=NOW) == ""  # type: ignore[arg-type]


def test_format_relative_explicit_now_injection() -> None:
    """Explicit now= injection produces deterministic output."""
    ts = "2026-05-14T21:59:30+00:00"  # 30 seconds before NOW
    result = format_relative(ts, now=NOW)
    assert result == "hace 30s"


def test_format_relative_no_now_does_not_raise() -> None:
    """Calling without now= (uses datetime.now(UTC)) does not raise."""
    ts = datetime.now(UTC).isoformat()
    result = format_relative(ts)  # no now= param
    assert result == "ahora"  # just created timestamp


def test_format_relative_z_suffix_timestamp() -> None:
    """Z-suffix ISO 8601 string is handled without error."""
    # Python 3.11+ parses Z suffix natively
    ts = "2026-05-14T21:59:00Z"  # 60 seconds before NOW
    result = format_relative(ts, now=NOW)
    assert result == "hace 1m"


def test_format_relative_datetime_object() -> None:
    """Passing a datetime object directly is supported."""
    from datetime import timedelta
    ts = NOW - timedelta(seconds=90)  # 90 seconds ago → "hace 1m"
    result = format_relative(ts, now=NOW)
    assert result == "hace 1m"


def test_format_relative_naive_datetime_object() -> None:
    """Naive datetime object is treated as UTC."""
    from datetime import timedelta
    ts = (NOW - timedelta(seconds=30)).replace(tzinfo=None)
    result = format_relative(ts, now=NOW)
    assert result == "hace 30s"
