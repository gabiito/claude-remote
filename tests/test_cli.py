"""claudio CLI — lifecycle verbs (WU-1). subprocess injected, never run."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_systemctl_argv() -> None:
    from claude_remote import cli

    assert cli.systemctl_argv("restart") == [
        "systemctl",
        "--user",
        "restart",
        "claude-remote.service",
    ]


def test_logs_argv() -> None:
    from claude_remote import cli

    assert cli.logs_argv() == [
        "journalctl",
        "--user",
        "-u",
        "claude-remote.service",
        "-f",
    ]


def test_build_argv_maps_verbs() -> None:
    from claude_remote import cli

    for v in ("start", "stop", "restart", "status"):
        assert cli.build_argv(v) == cli.systemctl_argv(v)
    assert cli.build_argv("logs") == cli.logs_argv()


def test_main_dispatches_to_runner() -> None:
    from claude_remote import cli

    seen: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    rc = cli.main(["restart"], runner=fake_runner)
    assert rc == 0
    assert seen == [["systemctl", "--user", "restart", "claude-remote.service"]]


def test_main_unknown_command_errors() -> None:
    from claude_remote import cli

    with pytest.raises(SystemExit):
        cli.main(["frobnicate"], runner=lambda _a: 0)


def test_entry_point_declared() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text()
    assert "[project.scripts]" in text
    assert 'claudio = "claude_remote.cli:main"' in text
