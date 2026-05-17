"""System / session / app metrics for the /metrics screen (roadmap #3).

Never raises: psutil hiccups must degrade to safe values, never 500 the
page. Values are point-in-time (no historical series).
"""

from __future__ import annotations

import os
import platform
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import psutil

from claude_remote.services.session_grouping import ACTIVE_STATUSES, aggregate_status

_GB = 1024**3
_T = TypeVar("_T")


def _safe(fn: Callable[[], _T], default: _T) -> _T:
    try:
        return fn()
    except Exception:  # noqa: BLE001 — metrics must never raise
        return default


def _host_label() -> str:
    cpu = platform.processor() or platform.machine() or "cpu"
    return f"{cpu} · {platform.system().upper() or 'UNKNOWN'}"


def collect_host(projects_root: Path) -> dict[str, Any]:
    """Point-in-time host metrics. Degrades gracefully on any psutil error."""
    vm = _safe(psutil.virtual_memory, None)
    sm = _safe(psutil.swap_memory, None)
    du = _safe(lambda: psutil.disk_usage(str(projects_root)), None)
    load = _safe(os.getloadavg, (0.0, 0.0, 0.0))
    boot = _safe(psutil.boot_time, time.time())

    return {
        "cpu_percent": round(_safe(lambda: psutil.cpu_percent(interval=None), 0.0), 1),
        "cpu_count": _safe(lambda: psutil.cpu_count(logical=True), 0) or 0,
        "load": {
            "1m": round(load[0], 2),
            "5m": round(load[1], 2),
            "15m": round(load[2], 2),
        },
        "ram": {
            "used_gb": round(vm.used / _GB, 1) if vm else 0.0,
            "total_gb": round(vm.total / _GB, 1) if vm else 0.0,
            "percent": round(vm.percent, 1) if vm else 0.0,
        },
        "swap": {"used_gb": round(sm.used / _GB, 1) if sm else 0.0},
        "disk": {
            "free_gb": round(du.free / _GB, 1) if du else 0.0,
            "total_gb": round(du.total / _GB, 1) if du else 0.0,
            "percent": round(du.percent, 1) if du else 0.0,
        },
        "uptime_seconds": max(0, int(time.time() - boot)),
        "host": _host_label(),
    }


def collect_session(pane_pid: int | None) -> dict[str, Any]:
    """CPU%/RSS/uptime for a session's process tree (pane shell + children).

    Never raises: a dead/missing PID degrades to a not-alive zero result.
    CPU% is best-effort (cpu_percent(interval=None) — relative to last call).
    """
    dead = {"alive": False, "cpu_percent": 0.0, "rss_mb": 0.0, "uptime_seconds": 0}
    if pane_pid is None:
        return dead
    try:
        proc = psutil.Process(pane_pid)
        procs = [proc, *proc.children(recursive=True)]
        rss = 0
        cpu = 0.0
        for p in procs:
            try:
                rss += p.memory_info().rss
                cpu += p.cpu_percent(interval=None)
            except Exception:  # noqa: BLE001 — a child can vanish mid-walk
                continue
        uptime = max(0, int(time.time() - proc.create_time()))
        return {
            "alive": True,
            "cpu_percent": round(cpu, 1),
            "rss_mb": round(rss / (1024**2), 1),
            "uptime_seconds": uptime,
        }
    except Exception:  # noqa: BLE001 — missing/dead PID
        return dead


_CSS_TOKEN = {"needs_input": "needs"}


def collect_app(
    cards: list[dict[str, Any]],
    events_repo: Any,
    push_repo: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    """App-level metrics from DB data already available (no psutil).

    Sessions total/live/status-breakdown, event totals + last-hour + by-type,
    push device count.
    """
    breakdown: Counter[str] = Counter()
    live = 0
    for card in cards:
        status = aggregate_status(card.get("instance_views", []))
        if status in ACTIVE_STATUSES:
            live += 1
        breakdown[_CSS_TOKEN.get(status, status)] += 1

    since_iso = (now - timedelta(hours=1)).isoformat()
    ev = _safe(lambda: events_repo.stats(since_iso), {"total": 0, "since": 0, "by_type": {}})
    devices = len(_safe(push_repo.list_all, []))  # pyright: ignore[reportUnknownArgumentType]

    return {
        "sessions": {
            "projects": len(cards),
            "live": live,
            "breakdown": dict(breakdown),
        },
        "events": {
            "total": int(ev.get("total", 0)),
            "last_hour": int(ev.get("since", 0)),
            "by_type": dict(ev.get("by_type", {})),
        },
        "push_devices": devices,
    }
