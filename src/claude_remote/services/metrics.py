"""System / session / app metrics for the /metrics screen (roadmap #3).

Never raises: psutil hiccups must degrade to safe values, never 500 the
page. Values are point-in-time (no historical series).
"""

from __future__ import annotations

import os
import platform
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import psutil

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
