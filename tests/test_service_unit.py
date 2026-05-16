"""systemd --user unit rendering for the keep-alive service (roadmap #1).

The unit text is generated in Python (testable); the Makefile only does the
systemctl glue. Paths are resolved at install time so the committed repo stays
portable across machines/users.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "render_service", _REPO / "deploy" / "render_service.py"
)
assert _SPEC and _SPEC.loader
render_service = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(render_service)


def _unit() -> str:
    return render_service.render_unit(
        repo_root=Path("/srv/claude-remote"),
        venv_dir=Path("/srv/claude-remote/.venv"),
    )


def test_unit_has_required_sections() -> None:
    u = _unit()
    assert "[Unit]" in u
    assert "[Service]" in u
    assert "[Install]" in u


def test_execstart_uses_venv_uvicorn_without_reload() -> None:
    u = _unit()
    assert "/srv/claude-remote/.venv/bin/uvicorn" in u
    assert "claude_remote.app:app" in u
    assert "--port 8000" in u
    # --reload is a dev-only flag; a managed service must not use it.
    assert "--reload" not in u


def test_restart_always_keeps_server_alive() -> None:
    u = _unit()
    assert "Restart=always" in u


def test_install_targets_default_target() -> None:
    """WantedBy=default.target → starts on user login."""
    assert "WantedBy=default.target" in _unit()


def test_workingdirectory_is_repo_root() -> None:
    assert "WorkingDirectory=/srv/claude-remote" in _unit()
