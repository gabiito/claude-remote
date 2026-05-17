"""Group home cards into ACTIVE SESSIONS vs PROJECTS (roadmap #2, WU-1).

Pure: no I/O, no clock. Operates on the card dicts the home route builds
(``project``, ``instance_views``, ``recent_events``). Status values are the
service-layer ones from ``derive_live_status`` (``needs_input``, not the CSS
``needs`` token — the template still maps via the ``status_token`` filter).
"""

from __future__ import annotations

from typing import Any

# Highest attention first. A project's aggregate status is the most
# attention-worthy status among its instances; empty → "stopped" (no session).
_PRIORITY: tuple[str, ...] = (
    "needs_input",
    "active",
    "running",
    "idle",
    "crashed",
    "stopped",
)
_PRIORITY_INDEX = {s: i for i, s in enumerate(_PRIORITY)}

# A project belongs to ACTIVE SESSIONS when it has a live console.
ACTIVE_STATUSES: frozenset[str] = frozenset({"needs_input", "active", "running", "idle"})


def filter_cards_by_domain(
    cards: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Return only cards in ``domain``. ``"all"`` / empty → unchanged.

    Server-side filtering replaces the old per-card Alpine x-show wrappers,
    which broke under the whole-list innerHTML poll swap.
    """
    if not domain or domain == "all":
        return list(cards)
    return [c for c in cards if c["project"].domain == domain]


def aggregate_status(instance_views: list[dict[str, Any]]) -> str:
    """Return the most attention-worthy live_status across a project's instances.

    Empty (no instances / no session) → ``"stopped"`` so it lands in PROJECTS.
    """
    best = "stopped"
    best_rank = _PRIORITY_INDEX["stopped"]
    for iv in instance_views:
        status = iv.get("live_status", "stopped")
        rank = _PRIORITY_INDEX.get(status, _PRIORITY_INDEX["stopped"])
        if rank < best_rank:
            best, best_rank = status, rank
    return best


def build_active_sessions(
    cards: list[dict[str, Any]], current_project_id: str
) -> list[dict[str, Any]]:
    """Rail entries for every project with a live console (roadmap #2 WU-2).

    One entry per ACTIVE_STATUSES project, sorted by attention priority then
    domain/name. ``status`` is the service value (e.g. ``needs_input``); the
    template maps it to the CSS token via the ``status_token`` filter.
    """
    entries: list[dict[str, Any]] = []
    for card in cards:
        status = aggregate_status(card.get("instance_views", []))
        if status not in ACTIVE_STATUSES:
            continue
        project = card["project"]
        ivs = card.get("instance_views", [])
        primary = next(
            (iv for iv in ivs if iv.get("live_status") not in ("stopped", "crashed")),
            None,
        )
        instance_id = (
            getattr(primary.get("instance"), "id", None) if primary else None
        )
        entries.append(
            {
                "project_id": project.id,
                "domain": project.domain,
                "name": project.name,
                "status": status,
                "is_current": project.id == current_project_id,
                "instance_id": instance_id,
            }
        )
    entries.sort(
        key=lambda e: (_PRIORITY_INDEX.get(e["status"], 99), e["domain"], e["name"])
    )
    return entries


def _latest_event_iso(card: dict[str, Any]) -> str:
    """Newest event timestamp (ISO 8601 sorts lexicographically). "" if none."""
    times = [
        getattr(e, "received_at", "") or "" for e in card.get("recent_events", [])
    ]
    return max(times) if times else ""


def group_and_sort_cards(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split cards into ``active`` and ``projects`` groups, each sorted.

    - active:   attention priority (needs_input > active > running > idle),
                then domain, then name.
    - projects: most-recent activity first (latest event ts DESC),
                then domain, then name; cards with no events sort last.
    """
    active: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    for card in cards:
        status = aggregate_status(card.get("instance_views", []))
        (active if status in ACTIVE_STATUSES else projects).append(card)

    active.sort(
        key=lambda c: (
            _PRIORITY_INDEX.get(aggregate_status(c["instance_views"]), 99),
            c["project"].domain,
            c["project"].name,
        )
    )
    projects.sort(
        key=lambda c: (
            _latest_event_iso(c) == "",  # cards without events last
            _negkey(_latest_event_iso(c)),
            c["project"].domain,
            c["project"].name,
        )
    )
    return {"active": active, "projects": projects}


class _negkey:
    """Reverse-order sort key for strings (most-recent ISO timestamp first)."""

    __slots__ = ("_s",)

    def __init__(self, s: str) -> None:
        self._s = s

    def __lt__(self, other: _negkey) -> bool:
        return self._s > other._s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _negkey) and self._s == other._s
