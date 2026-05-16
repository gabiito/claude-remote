"""Host metrics (roadmap #3 WU-1). psutil mocked for deterministic asserts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_collect_host_shape_and_values(tmp_path: Path) -> None:
    from claude_remote.services import metrics

    vm = MagicMock(used=22 * 1024**3, total=32 * 1024**3, percent=70.0)
    sm = MagicMock(used=int(0.3 * 1024**3))
    du = MagicMock(free=421 * 1024**3, total=1000 * 1024**3, percent=58.0)

    with (
        patch.object(metrics.psutil, "cpu_percent", return_value=64.0),
        patch.object(metrics.psutil, "cpu_count", return_value=10),
        patch.object(metrics.psutil, "virtual_memory", return_value=vm),
        patch.object(metrics.psutil, "swap_memory", return_value=sm),
        patch.object(metrics.psutil, "disk_usage", return_value=du),
        patch.object(metrics.psutil, "boot_time", return_value=0.0),
        patch.object(metrics.os, "getloadavg", return_value=(4.8, 3.9, 3.1)),
    ):
        h = metrics.collect_host(tmp_path)

    assert h["cpu_percent"] == 64.0
    assert h["cpu_count"] == 10
    assert h["load"] == {"1m": 4.8, "5m": 3.9, "15m": 3.1}
    assert round(h["ram"]["used_gb"], 1) == 22.0
    assert round(h["ram"]["total_gb"], 1) == 32.0
    assert h["ram"]["percent"] == 70.0
    assert round(h["swap"]["used_gb"], 1) == 0.3
    assert round(h["disk"]["free_gb"]) == 421
    assert h["disk"]["percent"] == 58.0
    assert h["uptime_seconds"] >= 0
    assert isinstance(h["host"], str) and "LINUX" in h["host"].upper()


def test_collect_host_never_raises_on_psutil_error(tmp_path: Path) -> None:
    """A psutil failure must degrade gracefully, not 500 the metrics page."""
    from claude_remote.services import metrics

    with patch.object(metrics.psutil, "cpu_percent", side_effect=RuntimeError("x")):
        h = metrics.collect_host(tmp_path)
    assert isinstance(h, dict)
    assert "cpu_percent" in h
