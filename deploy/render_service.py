"""Render the systemd --user unit for the claude-remote keep-alive service.

Roadmap #1: keep the server alive and auto-start it on login.

Usage (the Makefile does this for you)::

    python deploy/render_service.py > ~/.config/systemd/user/claude-remote.service

Paths are resolved from this file's location, so the committed repo stays
portable — nothing machine-specific is checked in; the concrete unit is
generated at install time.
"""

from __future__ import annotations

import sys
from pathlib import Path


def render_unit(repo_root: Path, venv_dir: Path) -> str:
    """Return the systemd --user unit text for the given checkout.

    - ExecStart uses the venv's uvicorn WITHOUT --reload (dev-only flag).
    - Restart=always keeps the server up across crashes/exits.
    - StartLimitIntervalSec=0 disables the start-rate lockout so a transient
      crash loop self-heals instead of getting stuck in `failed`.
    - WantedBy=default.target → starts on user login.
    """
    uvicorn = venv_dir / "bin" / "uvicorn"
    return f"""[Unit]
Description=claude-remote — manage Claude Code CLI instances from your phone
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={repo_root}
ExecStart={uvicorn} claude_remote.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    venv_dir = repo_root / ".venv"
    sys.stdout.write(render_unit(repo_root, venv_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
