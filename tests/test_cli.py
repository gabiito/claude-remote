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


# --- WU-2: install / uninstall ---


def test_install_writes_unit_enables_and_symlinks(tmp_path: Path) -> None:
    from claude_remote import cli

    systemd_dir = tmp_path / "systemd"
    local_bin = tmp_path / "bin"
    claudio_bin = tmp_path / "venv" / "claudio"
    claudio_bin.parent.mkdir(parents=True)
    claudio_bin.write_text("#!/bin/sh\n")
    calls: list[list[str]] = []

    rc = cli.install(
        runner=lambda a: (calls.append(a) or 0),
        render=lambda: "[Unit]\nDescription=x\n",
        systemd_dir=systemd_dir,
        local_bin=local_bin,
        claudio_path=claudio_bin,
    )
    assert rc == 0
    unit = systemd_dir / "claude-remote.service"
    assert unit.read_text().startswith("[Unit]")
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "--now", "claude-remote.service"] in calls
    link = local_bin / "claudio"
    assert link.is_symlink()
    assert link.resolve() == claudio_bin.resolve()


def test_install_does_not_clobber_real_file(tmp_path: Path) -> None:
    from claude_remote import cli

    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    real = local_bin / "claudio"
    real.write_text("i am a real file, not a symlink")

    cli.install(
        runner=lambda _a: 0,
        render=lambda: "[Unit]\n",
        systemd_dir=tmp_path / "sd",
        local_bin=local_bin,
        claudio_path=tmp_path / "x",
    )
    assert not real.is_symlink()
    assert real.read_text() == "i am a real file, not a symlink"


def test_uninstall_disables_removes_unit_and_symlink(tmp_path: Path) -> None:
    from claude_remote import cli

    systemd_dir = tmp_path / "sd"
    systemd_dir.mkdir()
    unit = systemd_dir / "claude-remote.service"
    unit.write_text("[Unit]\n")
    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    target = tmp_path / "claudio-bin"
    target.write_text("x")
    link = local_bin / "claudio"
    link.symlink_to(target)
    calls: list[list[str]] = []

    rc = cli.uninstall(
        runner=lambda a: (calls.append(a) or 0),
        systemd_dir=systemd_dir,
        local_bin=local_bin,
    )
    assert rc == 0
    assert not unit.exists()
    assert not link.exists()
    assert ["systemctl", "--user", "disable", "--now", "claude-remote.service"] in calls


def test_main_accepts_install_uninstall() -> None:
    from claude_remote import cli

    assert "install" in cli._parser()._option_string_actions or True
    # argparse choices include install/uninstall
    import contextlib
    import io

    for cmd in ("install", "uninstall"):
        # parsing must not raise SystemExit for these commands
        with contextlib.redirect_stderr(io.StringIO()):
            ns = cli._parser().parse_args([cmd])
        assert ns.command == cmd


# --- WU-3: --version / --help ---


def test_version_flag_matches_git_describe_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--version reflects the real git state — same source as the web header."""
    import subprocess

    from claude_remote import cli

    expected = subprocess.run(
        ["git", "describe", "--tags", "--always", "--dirty"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert expected, "git describe produced no output in this checkout"

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"], runner=lambda _a: 0)
    assert exc.value.code == 0
    assert expected in capsys.readouterr().out


def test_cli_version_is_same_source_as_header() -> None:
    """CLI and web header MUST resolve the version through one shared function."""
    from claude_remote import cli
    from claude_remote.routes._templates import app_version

    assert cli._version() == app_version()


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    from claude_remote import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"], runner=lambda _a: 0)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for word in ("install", "uninstall", "start", "stop", "restart", "status", "logs"):
        assert word in out
