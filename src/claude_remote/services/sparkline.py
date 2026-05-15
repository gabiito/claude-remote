"""Sparkline normalization helper.

compute_sparkline() fetches event counts from the last N hours and normalises
them to pixel heights suitable for inline ``style="height: Npx;"`` attributes.
"""

from __future__ import annotations

from claude_remote.db.events import EventsRepository


def normalize_sparkline(
    counts: list[int],
    *,
    max_height: int = 14,
    min_height: int = 2,
) -> list[int]:
    """Linearly scale count values to bar heights in pixels.

    Args:
        counts: raw event counts per bucket (any length).
        max_height: pixel height assigned to the peak bucket.
        min_height: pixel height for zero-count buckets.

    Returns:
        List of integers (pixel heights) same length as counts.
        All zeros → all ``min_height``.
        Peak bucket → ``max_height``.
    """
    if not counts:
        return []
    peak = max(counts)
    if peak == 0:
        return [min_height] * len(counts)
    return [max(min_height, round(c * max_height / peak)) for c in counts]


def compute_sparkline(
    events_repo: EventsRepository,
    *,
    buckets: int = 8,
) -> list[int]:
    """Return normalised bar heights for the last ``buckets`` hours.

    Args:
        events_repo: live EventsRepository (or test double).
        buckets: number of hourly bars to return (default 8).

    Returns:
        List of ``buckets`` ints (pixel heights 2–14).
    """
    counts_24h = events_repo.count_per_hour_last_24h()
    recent = counts_24h[-buckets:]  # last N buckets
    return normalize_sparkline(recent)
