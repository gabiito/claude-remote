"""Systemd unit rendering (auth/#1 service) — PATH baking.

systemd --user services do NOT inherit the interactive shell PATH. Without
an explicit Environment=PATH the service can't find `claude` (npm /
~/.local/bin / nvm / linuxbrew), so a launched tmux pane dies instantly →
the UI shows "[Session unavailable]". The unit must carry the installing
user's PATH.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load_render():
    script = _REPO / "deploy" / "render_service.py"
    spec = importlib.util.spec_from_file_location("_rs", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_unit_bakes_path_when_given() -> None:
    mod = _load_render()
    unit = mod.render_unit(_REPO, _REPO / ".venv", path_env="/x/bin:/y/bin")
    assert "Environment=PATH=/x/bin:/y/bin" in unit
    assert "[Service]" in unit and "ExecStart=" in unit


def test_render_unit_omits_environment_when_no_path() -> None:
    mod = _load_render()
    unit = mod.render_unit(_REPO, _REPO / ".venv", path_env=None)
    assert "Environment=PATH=" not in unit


def test_default_render_bakes_current_path() -> None:
    from claude_remote import cli

    unit = cli._default_render()
    assert "Environment=PATH=" in unit
    assert os.environ["PATH"] in unit
