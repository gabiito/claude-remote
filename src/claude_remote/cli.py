"""``claudio`` — manage the claude-remote systemd --user service.

Exposed as a console script (see [project.scripts]). Wraps systemctl so the
daily lifecycle is ergonomic: ``claudio start|stop|restart|status|logs``
(install/uninstall added in WU-2). These manage the installed service — for
foreground dev use ``make run``.
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable, Sequence

SERVICE = "claude-remote.service"

_VERBS = ("start", "stop", "restart", "status")


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
        choices=[*_VERBS, "logs"],
        help="start | stop | restart | status | logs",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    runner: Callable[[list[str]], int] = subprocess.call,
) -> int:
    args = _parser().parse_args(argv)
    return runner(build_argv(args.command))
