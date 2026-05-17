"""``claudio`` — manage the claude-remote systemd --user service.

Exposed as a console script (see [project.scripts]). Wraps systemctl so the
daily lifecycle is ergonomic: ``claudio start|stop|restart|status|logs``
(install/uninstall added in WU-2). These manage the installed service — for
foreground dev use ``make run``.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

SERVICE = "claude-remote.service"

_VERBS = ("start", "stop", "restart", "status")

Runner = Callable[[list[str]], int]


def _repo_root() -> Path:
    # src/claude_remote/cli.py → parents: claude_remote, src, repo_root
    return Path(__file__).resolve().parents[2]


def _default_render() -> str:
    """Render the systemd unit via the single source (deploy/render_service.py)."""
    script = _repo_root() / "deploy" / "render_service.py"
    if not script.exists():
        raise FileNotFoundError(
            f"{script} not found — claudio install needs the git checkout layout"
        )
    spec = importlib.util.spec_from_file_location("_render_service", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = _repo_root()
    return mod.render_unit(root, root / ".venv")  # type: ignore[no-any-return]


def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _claudio_path() -> Path:
    found = shutil.which("claudio")
    return Path(found) if found else Path(sys.argv[0]).resolve()


def install(
    *,
    runner: Runner = subprocess.call,
    render: Callable[[], str] = _default_render,
    systemd_dir: Path | None = None,
    local_bin: Path | None = None,
    claudio_path: Path | None = None,
) -> int:
    sd = systemd_dir if systemd_dir is not None else _systemd_dir()
    lb = local_bin if local_bin is not None else _local_bin()
    cp = claudio_path if claudio_path is not None else _claudio_path()

    sd.mkdir(parents=True, exist_ok=True)
    (sd / SERVICE).write_text(render())
    runner(["systemctl", "--user", "daemon-reload"])
    runner(["systemctl", "--user", "enable", "--now", SERVICE])
    # Survive logout/boot; best-effort (may need polkit/sudo on some distros).
    runner(["loginctl", "enable-linger", getpass.getuser()])

    lb.mkdir(parents=True, exist_ok=True)
    link = lb / "claudio"
    if link.is_symlink() or not link.exists():
        if link.is_symlink():
            link.unlink()
        if not link.exists():
            link.symlink_to(cp)
    else:
        print(f"NOTE: {link} exists and is not a symlink — left untouched.")
    print(f"Installed. Try: claudio status   (ensure {lb} is on PATH)")
    return 0


def uninstall(
    *,
    runner: Runner = subprocess.call,
    systemd_dir: Path | None = None,
    local_bin: Path | None = None,
) -> int:
    sd = systemd_dir if systemd_dir is not None else _systemd_dir()
    lb = local_bin if local_bin is not None else _local_bin()

    runner(["systemctl", "--user", "disable", "--now", SERVICE])
    (sd / SERVICE).unlink(missing_ok=True)
    runner(["systemctl", "--user", "daemon-reload"])
    link = lb / "claudio"
    if link.is_symlink():
        link.unlink()
    print("Removed.")
    return 0


def systemctl_argv(verb: str) -> list[str]:
    return ["systemctl", "--user", verb, SERVICE]


def logs_argv() -> list[str]:
    return ["journalctl", "--user", "-u", SERVICE, "-f"]


def build_argv(command: str) -> list[str]:
    if command == "logs":
        return logs_argv()
    if command in _VERBS:
        return systemctl_argv(command)
    raise ValueError(f"unknown command: {command}")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claudio", description=__doc__)
    p.add_argument(
        "command",
        choices=[*_VERBS, "logs", "install", "uninstall"],
        help="install | uninstall | start | stop | restart | status | logs",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    runner: Callable[[list[str]], int] = subprocess.call,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "install":
        return install(runner=runner)
    if args.command == "uninstall":
        return uninstall(runner=runner)
    return runner(build_argv(args.command))
