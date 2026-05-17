"""Session grouping for the home list (roadmap #2, WU-1).

Pure helpers: aggregate a project's instance statuses, then split cards into
the ACTIVE SESSIONS group (a live console) vs the PROJECTS group (inert).
"""

from __future__ import annotations

from types import SimpleNamespace


def _card(domain: str, name: str, statuses: list[str], event_times: list[str] | None = None):
    return {
        "project": SimpleNamespace(domain=domain, name=name, id=f"{domain}-{name}"),
        "instance_views": [
            {"instance": SimpleNamespace(id=f"{domain}-{name}-{i}"), "live_status": s}
            for i, s in enumerate(statuses)
        ],
        "recent_events": [SimpleNamespace(received_at=t) for t in (event_times or [])],
    }


# ---------------------------------------------------------------------------
# aggregate_status
# ---------------------------------------------------------------------------


class TestAggregateStatus:
    def test_empty_instances_is_stopped(self) -> None:
        from claude_remote.services.session_grouping import aggregate_status

        assert aggregate_status([]) == "stopped"

    def test_needs_input_wins_over_everything(self) -> None:
        from claude_remote.services.session_grouping import aggregate_status

        ivs = [{"live_status": "idle"}, {"live_status": "needs_input"}, {"live_status": "active"}]
        assert aggregate_status(ivs) == "needs_input"

    def test_active_over_running_idle(self) -> None:
        from claude_remote.services.session_grouping import aggregate_status

        assert aggregate_status([{"live_status": "running"}, {"live_status": "active"}]) == "active"

    def test_crashed_over_stopped_but_below_live(self) -> None:
        from claude_remote.services.session_grouping import aggregate_status

        crashed_over_stopped = [{"live_status": "stopped"}, {"live_status": "crashed"}]
        assert aggregate_status(crashed_over_stopped) == "crashed"
        live_over_crashed = [{"live_status": "crashed"}, {"live_status": "idle"}]
        assert aggregate_status(live_over_crashed) == "idle"


# ---------------------------------------------------------------------------
# group_and_sort_cards
# ---------------------------------------------------------------------------


class TestFilterByDomain:
    def test_all_returns_everything(self) -> None:
        from claude_remote.services.session_grouping import filter_cards_by_domain

        cards = [_card("wooli", "a", ["active"]), _card("sandbox", "b", ["stopped"])]
        assert len(filter_cards_by_domain(cards, "all")) == 2
        assert len(filter_cards_by_domain(cards, "")) == 2

    def test_specific_domain_filters(self) -> None:
        from claude_remote.services.session_grouping import filter_cards_by_domain

        cards = [
            _card("wooli", "a", ["active"]),
            _card("wooli", "b", ["stopped"]),
            _card("sandbox", "c", ["idle"]),
        ]
        out = filter_cards_by_domain(cards, "wooli")
        assert {c["project"].name for c in out} == {"a", "b"}

    def test_unknown_domain_empty(self) -> None:
        from claude_remote.services.session_grouping import filter_cards_by_domain

        cards = [_card("wooli", "a", ["active"])]
        assert filter_cards_by_domain(cards, "nope") == []


class TestBuildActiveSessions:
    def test_only_active_included(self) -> None:
        from claude_remote.services.session_grouping import build_active_sessions

        cards = [
            _card("wooli", "landing", ["running"]),
            _card("wooli", "migrations", ["crashed"]),
            _card("sandbox", "exp", []),  # no session
        ]
        out = build_active_sessions(cards, current_project_id="x")
        assert {s["name"] for s in out} == {"landing"}

    def test_is_current_flag(self) -> None:
        from claude_remote.services.session_grouping import build_active_sessions

        cards = [_card("wooli", "a", ["active"]), _card("wooli", "b", ["running"])]
        out = build_active_sessions(cards, current_project_id="wooli-a")
        cur = {s["name"]: s["is_current"] for s in out}
        assert cur == {"a": True, "b": False}

    def test_sorted_by_attention_priority(self) -> None:
        from claude_remote.services.session_grouping import build_active_sessions

        cards = [
            _card("d", "i", ["idle"]),
            _card("d", "n", ["needs_input"]),
            _card("d", "r", ["running"]),
        ]
        out = build_active_sessions(cards, current_project_id="")
        assert [s["name"] for s in out] == ["n", "r", "i"]

    def test_entry_shape(self) -> None:
        from claude_remote.services.session_grouping import build_active_sessions

        out = build_active_sessions(
            [_card("wooli", "landing", ["needs_input"])], current_project_id="wooli-landing"
        )
        s = out[0]
        assert s["project_id"] == "wooli-landing"
        assert s["domain"] == "wooli"
        assert s["name"] == "landing"
        assert s["status"] == "needs_input"
        assert s["is_current"] is True

    def test_entry_includes_active_instance_id(self) -> None:
        """The rail 'x' needs the active instance id to POST a stop."""
        from claude_remote.services.session_grouping import build_active_sessions

        out = build_active_sessions(
            [_card("wooli", "landing", ["idle", "needs_input"])],
            current_project_id="x",
        )
        # First non-terminal instance of the project.
        assert out[0]["instance_id"] == "wooli-landing-0"


class TestGroupAndSort:
    def test_split_active_vs_projects(self) -> None:
        from claude_remote.services.session_grouping import group_and_sort_cards

        cards = [
            _card("wooli", "landing", ["needs_input"]),
            _card("wooli", "migrations", ["crashed"]),
            _card("sandbox", "scratch", ["idle"]),
            _card("sandbox", "exp", []),  # no session → projects
        ]
        g = group_and_sort_cards(cards)
        active_names = [c["project"].name for c in g["active"]]
        proj_names = [c["project"].name for c in g["projects"]]
        assert set(active_names) == {"landing", "scratch"}
        assert set(proj_names) == {"migrations", "exp"}

    def test_active_sorted_by_attention_priority(self) -> None:
        from claude_remote.services.session_grouping import group_and_sort_cards

        cards = [
            _card("d", "idle1", ["idle"]),
            _card("d", "run1", ["running"]),
            _card("d", "need1", ["needs_input"]),
            _card("d", "act1", ["active"]),
        ]
        g = group_and_sort_cards(cards)
        assert [c["project"].name for c in g["active"]] == ["need1", "act1", "run1", "idle1"]

    def test_projects_sorted_recent_first(self) -> None:
        from claude_remote.services.session_grouping import group_and_sort_cards

        cards = [
            _card("d", "old", ["stopped"], ["2026-05-01T10:00:00+00:00"]),
            _card("d", "new", ["crashed"], ["2026-05-16T10:00:00+00:00"]),
            _card("d", "noev", ["stopped"], []),
        ]
        g = group_and_sort_cards(cards)
        names = [c["project"].name for c in g["projects"]]
        assert names.index("new") < names.index("old") < names.index("noev")

    def test_counts_match_lengths(self) -> None:
        from claude_remote.services.session_grouping import group_and_sort_cards

        cards = [_card("d", "a", ["active"]), _card("d", "b", ["stopped"])]
        g = group_and_sort_cards(cards)
        assert len(g["active"]) == 1
        assert len(g["projects"]) == 1
