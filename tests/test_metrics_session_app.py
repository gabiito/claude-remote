"""Per-session + app metrics (roadmap #3 WU-2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# collect_session
# ---------------------------------------------------------------------------


def test_collect_session_none_pid_graceful() -> None:
    from claude_remote.services import metrics

    s = metrics.collect_session(None)
    assert s == {"alive": False, "cpu_percent": 0.0, "rss_mb": 0.0, "uptime_seconds": 0}


def test_collect_session_dead_pid_graceful() -> None:
    from claude_remote.services import metrics

    with patch.object(metrics.psutil, "Process", side_effect=Exception("no such pid")):
        s = metrics.collect_session(999999)
    assert s["alive"] is False
    assert s["rss_mb"] == 0.0


def test_collect_session_sums_process_tree() -> None:
    from claude_remote.services import metrics

    def _proc(rss: int, cpu: float):
        m = MagicMock()
        m.memory_info.return_value = SimpleNamespace(rss=rss)
        m.cpu_percent.return_value = cpu
        m.create_time.return_value = 0.0
        return m

    parent = _proc(100 * 1024**2, 5.0)
    child = _proc(50 * 1024**2, 80.0)
    parent.children.return_value = [child]

    with patch.object(metrics.psutil, "Process", return_value=parent):
        s = metrics.collect_session(1234)

    assert s["alive"] is True
    assert round(s["rss_mb"]) == 150  # 100 + 50 MB
    assert s["cpu_percent"] == 85.0   # 5 + 80
    assert s["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# collect_app
# ---------------------------------------------------------------------------


def _card(domain: str, name: str, statuses: list[str]):
    return {
        "project": SimpleNamespace(domain=domain, name=name, id=f"{domain}-{name}"),
        "instance_views": [{"live_status": s} for s in statuses],
        "recent_events": [],
    }


def test_collect_app_aggregates() -> None:
    from claude_remote.services import metrics

    cards = [
        _card("w", "a", ["needs_input"]),
        _card("w", "b", ["active"]),
        _card("w", "c", ["running"]),
        _card("s", "d", ["idle"]),
        _card("s", "e", ["crashed"]),
        _card("s", "f", []),  # no session → stopped
    ]
    events_repo = MagicMock()
    events_repo.stats.return_value = {
        "total": 1284,
        "since": 72,
        "by_type": {"PreToolUse": 28, "Stop": 6},
    }
    push_repo = MagicMock()
    push_repo.list_all.return_value = [object(), object()]

    app = metrics.collect_app(
        cards, events_repo, push_repo, now=datetime(2026, 5, 16, tzinfo=UTC)
    )

    assert app["sessions"]["projects"] == 6
    assert app["sessions"]["live"] == 4  # needs/active/running/idle
    bd = app["sessions"]["breakdown"]
    assert bd["needs"] == 1 and bd["active"] == 1 and bd["running"] == 1
    assert bd["idle"] == 1 and bd["crashed"] == 1 and bd["stopped"] == 1
    assert app["events"]["total"] == 1284
    assert app["events"]["last_hour"] == 72
    assert app["events"]["by_type"]["PreToolUse"] == 28
    assert app["push_devices"] == 2
    # events_repo.stats called with an ISO timestamp ~1h before now
    since_arg = events_repo.stats.call_args.args[0]
    assert since_arg.startswith("2026-05-15T23:")
