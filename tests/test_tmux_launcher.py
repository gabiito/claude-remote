"""Red tests for WU-4 — TmuxLauncher launch/stop/reconcile.

All tests use FakeTmuxAdapter + a tmp SQLite DB with both migrations applied.
No tmux binary required.

Fixture strategy:
  - ``db_path``        — fresh migrated tmp DB per test
  - ``fake_adapter``   — FakeTmuxAdapter instance (reset per test)
  - ``instances_repo`` — InstancesRepository wired to the DB
  - ``projects_repo``  — ProjectsRepository wired to the DB
  - ``launcher``       — TmuxLauncher wired with the above
  - ``proj_id``        — a project row inserted and ready to use
"""

import re
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.services.exceptions import (
    EmptyCommandError,
    InstanceAlreadyRunningError,
    InstanceNotFoundError,
    ProjectNotFoundError,
    TmuxOperationError,
)
from claude_remote.services.tmux_adapter import FakeTmuxAdapter
from claude_remote.services.tmux_launcher import TmuxLauncher

SESSION_NAME_RE = re.compile(r"^claude-remote-[a-z0-9-]+-[0-9a-f]{8}$")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "launcher_test.db"
    apply_migrations(path, MIGRATIONS_DIR)
    return path


def _factory(db_path: Path):
    return lambda: get_connection_for(db_path)


@pytest.fixture()
def fake_adapter() -> FakeTmuxAdapter:
    return FakeTmuxAdapter()


@pytest.fixture()
def instances_repo(db_path: Path) -> InstancesRepository:
    return InstancesRepository(connection_factory=_factory(db_path))


@pytest.fixture()
def projects_repo(db_path: Path) -> ProjectsRepository:
    return ProjectsRepository(connection_factory=_factory(db_path))


@pytest.fixture()
def launcher(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
) -> TmuxLauncher:
    return TmuxLauncher(
        adapter=fake_adapter,
        instances_repo=instances_repo,
        projects_repo=projects_repo,
    )


@pytest.fixture()
def proj_id(projects_repo: ProjectsRepository, tmp_path: Path) -> str:
    """Insert a project and return its id."""
    p = tmp_path / "sandbox" / "my-project"
    p.mkdir(parents=True, exist_ok=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="My Project", slug="my-project", path=p, domain="sandbox"
        )
    )
    return proj.id


# ---------------------------------------------------------------------------
# launch — happy path
# ---------------------------------------------------------------------------


def test_launch_happy_path(launcher: TmuxLauncher, proj_id: str) -> None:
    """Happy-path launch: project exists, no active instance → running."""
    inst = launcher.launch(proj_id)
    assert inst.status == "running"
    assert inst.pane_pid is not None
    assert inst.pane_pid > 0
    assert inst.project_id == proj_id


def test_launch_session_name_format(launcher: TmuxLauncher, proj_id: str) -> None:
    """Session name matches claude-remote-{slug}-{8-hex-chars}."""
    inst = launcher.launch(proj_id)
    assert SESSION_NAME_RE.match(inst.tmux_session_name), (
        f"session name {inst.tmux_session_name!r} does not match expected format"
    )


def test_launch_defaults_to_claude_command(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    """No command arg → adapter called with command='claude'."""
    launcher.launch(proj_id)
    create_calls = [c for c in fake_adapter.calls if c[0] == "create_session"]
    assert len(create_calls) == 1
    assert create_calls[0][1]["command"] == "claude"


def test_launch_command_override(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    """Custom command is passed through to adapter.create_session."""
    launcher.launch(proj_id, command="bash -c 'sleep 1'")
    create_calls = [c for c in fake_adapter.calls if c[0] == "create_session"]
    assert create_calls[0][1]["command"] == "bash -c 'sleep 1'"


# ---------------------------------------------------------------------------
# launch — validation errors
# ---------------------------------------------------------------------------


def test_launch_empty_command_raises(launcher: TmuxLauncher, proj_id: str) -> None:
    with pytest.raises(EmptyCommandError):
        launcher.launch(proj_id, command="")


def test_launch_blank_command_raises(launcher: TmuxLauncher, proj_id: str) -> None:
    with pytest.raises(EmptyCommandError):
        launcher.launch(proj_id, command="   ")


def test_launch_none_command_uses_default(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    """command=None is NOT an EmptyCommandError — defaults to 'claude'."""
    inst = launcher.launch(proj_id, command=None)
    assert inst.status == "running"
    create_calls = [c for c in fake_adapter.calls if c[0] == "create_session"]
    assert create_calls[0][1]["command"] == "claude"


def test_launch_missing_project_raises(launcher: TmuxLauncher) -> None:
    with pytest.raises(ProjectNotFoundError):
        launcher.launch("nonexistent-project-id")


# ---------------------------------------------------------------------------
# launch — already running / reconciliation
# ---------------------------------------------------------------------------


def test_launch_already_running_raises(launcher: TmuxLauncher, proj_id: str) -> None:
    """Second launch for same project raises InstanceAlreadyRunningError."""
    launcher.launch(proj_id)
    with pytest.raises(InstanceAlreadyRunningError) as exc_info:
        launcher.launch(proj_id)
    assert exc_info.value.status in ("starting", "running")


def test_launch_after_stopped_instance_succeeds(
    launcher: TmuxLauncher,
    instances_repo: InstancesRepository,
    proj_id: str,
) -> None:
    """Stopped instance does not block new launch (new row, history preserved)."""
    first = launcher.launch(proj_id)
    launcher.stop(first.id)

    second = launcher.launch(proj_id)
    assert second.status == "running"
    assert second.id != first.id


def test_launch_after_reconcile_unblocks(
    launcher: TmuxLauncher,
    fake_adapter: FakeTmuxAdapter,
    proj_id: str,
) -> None:
    """If running instance's session dies externally, reconciliation unblocks launch."""
    first = launcher.launch(proj_id)
    fake_adapter.kill_session_externally(first.tmux_session_name)

    second = launcher.launch(proj_id)
    assert second.status == "running"
    assert second.id != first.id


def test_launch_adapter_failure_marks_starting_row(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    proj_id: str,
    tmp_path: Path,
) -> None:
    """When adapter.create_session raises, the 'starting' row stays (for audit trail)."""
    # Patch the adapter to always fail
    def _always_fail(name: str, cwd: Path, command: str) -> int | None:
        # Record the call then raise
        fake_adapter.calls.append(
            ("create_session", {"name": name, "cwd": cwd, "command": command})
        )
        raise TmuxOperationError("create_session", RuntimeError("tmux unavailable"))

    fake_adapter.create_session = _always_fail  # type: ignore[method-assign]

    bad_launcher = TmuxLauncher(
        adapter=fake_adapter,
        instances_repo=instances_repo,
        projects_repo=projects_repo,
    )

    with pytest.raises(TmuxOperationError):
        bad_launcher.launch(proj_id)

    # The 'starting' row should still exist (not deleted)
    all_instances = instances_repo.list_all()
    assert len(all_instances) == 1
    assert all_instances[0].status == "starting"


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_running_instance(launcher: TmuxLauncher, proj_id: str) -> None:
    inst = launcher.launch(proj_id)
    stopped = launcher.stop(inst.id)
    assert stopped.status == "stopped"
    assert stopped.stopped_at is not None


def test_stop_kills_tmux_session(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    launcher.stop(inst.id)
    assert not fake_adapter.session_exists(inst.tmux_session_name)


def test_stop_already_stopped_is_idempotent(
    launcher: TmuxLauncher, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    stopped = launcher.stop(inst.id)
    stopped_again = launcher.stop(inst.id)
    assert stopped_again.id == stopped.id
    assert stopped_again.status == "stopped"


def test_stop_already_crashed_is_idempotent(
    launcher: TmuxLauncher,
    fake_adapter: FakeTmuxAdapter,
    proj_id: str,
) -> None:
    inst = launcher.launch(proj_id)
    crashed = launcher.reconcile(inst)
    # reconcile won't crash it unless session is dead, so kill externally first
    fake_adapter.kill_session_externally(inst.tmux_session_name)
    crashed = launcher.reconcile(instances_repo_get_fresh(launcher, inst.id))
    result = launcher.stop(crashed.id)
    assert result.status == "crashed"  # unchanged


def instances_repo_get_fresh(launcher: TmuxLauncher, instance_id: str):
    """Helper: get fresh instance from launcher's repo (for test clarity)."""
    return launcher._instances.get(instance_id)  # noqa: SLF001


def test_stop_not_found_raises(launcher: TmuxLauncher) -> None:
    with pytest.raises(InstanceNotFoundError):
        launcher.stop("nonexistent-id")


def test_stop_when_session_already_gone(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    """kill_session returns False when session is already dead — stop still succeeds."""
    inst = launcher.launch(proj_id)
    fake_adapter.kill_session_externally(inst.tmux_session_name)
    stopped = launcher.stop(inst.id)
    assert stopped.status == "stopped"


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def test_reconcile_running_to_crashed(
    launcher: TmuxLauncher, fake_adapter: FakeTmuxAdapter, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    fake_adapter.kill_session_externally(inst.tmux_session_name)
    reconciled = launcher.reconcile(inst)
    assert reconciled.status == "crashed"
    assert reconciled.stopped_at is not None


def test_reconcile_running_session_alive_no_change(
    launcher: TmuxLauncher, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    reconciled = launcher.reconcile(inst)
    assert reconciled.status == "running"


def test_reconcile_stopped_instance_unchanged(
    launcher: TmuxLauncher, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    stopped = launcher.stop(inst.id)
    reconciled = launcher.reconcile(stopped)
    assert reconciled.status == "stopped"


def test_reconcile_orphan_session_no_db_change(
    launcher: TmuxLauncher,
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    proj_id: str,
) -> None:
    """DB says stopped but session exists → leave DB unchanged (inverse drift, locked Q4)."""
    inst = launcher.launch(proj_id)
    stopped = launcher.stop(inst.id)
    # Manually recreate the session in the fake (simulating orphan)
    from pathlib import Path as _Path
    fake_adapter._sessions[inst.tmux_session_name] = \
        fake_adapter._sessions.get(inst.tmux_session_name) or \
        type("_FakeSession", (), {"cwd": _Path("/tmp"), "command": "bash", "pane_pid": 9999})()  # type: ignore[assignment]

    reconciled = launcher.reconcile(stopped)
    assert reconciled.status == "stopped"  # unchanged — no flip to crashed


# ---------------------------------------------------------------------------
# reconcile_all
# ---------------------------------------------------------------------------


def test_reconcile_all_heals_crashed_instance(
    launcher: TmuxLauncher,
    fake_adapter: FakeTmuxAdapter,
    proj_id: str,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """reconcile_all marks a session-dead running instance as crashed."""
    # Create second project for second instance
    p2 = tmp_path / "sandbox" / "proj2"
    p2.mkdir(parents=True, exist_ok=True)
    proj2 = projects_repo.create(
        project_create=ProjectCreate(name="proj2", slug="proj2", path=p2, domain="sandbox")
    )

    inst1 = launcher.launch(proj_id)
    inst2 = launcher.launch(proj2.id)

    # Kill inst1's session externally
    fake_adapter.kill_session_externally(inst1.tmux_session_name)

    results = launcher.reconcile_all()
    by_id = {i.id: i for i in results}

    assert by_id[inst1.id].status == "crashed"
    assert by_id[inst2.id].status == "running"


def test_reconcile_all_empty_returns_empty(launcher: TmuxLauncher) -> None:
    assert launcher.reconcile_all() == []


# ---------------------------------------------------------------------------
# get_with_reconcile
# ---------------------------------------------------------------------------


def test_get_with_reconcile_found(
    launcher: TmuxLauncher, proj_id: str
) -> None:
    inst = launcher.launch(proj_id)
    result = launcher.get_with_reconcile(inst.id)
    assert result is not None
    assert result.id == inst.id
    assert result.status == "running"


def test_get_with_reconcile_reconciles_drift(
    launcher: TmuxLauncher,
    fake_adapter: FakeTmuxAdapter,
    proj_id: str,
) -> None:
    inst = launcher.launch(proj_id)
    fake_adapter.kill_session_externally(inst.tmux_session_name)
    result = launcher.get_with_reconcile(inst.id)
    assert result is not None
    assert result.status == "crashed"


def test_get_with_reconcile_not_found_returns_none(launcher: TmuxLauncher) -> None:
    result = launcher.get_with_reconcile("nonexistent-id")
    assert result is None


# ---------------------------------------------------------------------------
# WU-5: Launcher writes settings.json BEFORE tmux session creation
# ---------------------------------------------------------------------------


def _make_launcher_with_hooks(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    hooks_base_url: str = "http://localhost:8000",
) -> TmuxLauncher:
    """Construct a TmuxLauncher with hooks_base_url wired in."""
    return TmuxLauncher(
        adapter=fake_adapter,
        instances_repo=instances_repo,
        projects_repo=projects_repo,
        hooks_base_url=hooks_base_url,
    )


def test_launch_writes_settings_json_before_session_created(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """apply_hooks_to_settings must be called BEFORE adapter.create_session.

    We verify this by monkeypatching create_session to check that the
    .claude/settings.json file already exists when the adapter is called.
    """
    import json

    project_dir = tmp_path / "sandbox" / "launch-test"
    project_dir.mkdir(parents=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="launch-test", slug="launch-test", path=project_dir, domain="sandbox"
        )
    )

    settings_existed_before_create = []

    original_create = fake_adapter.create_session

    def _recording_create(name, cwd, command):
        settings_path = project_dir / ".claude" / "settings.json"
        settings_existed_before_create.append(settings_path.exists())
        return original_create(name, cwd, command)

    fake_adapter.create_session = _recording_create  # type: ignore[method-assign]

    launcher = _make_launcher_with_hooks(fake_adapter, instances_repo, projects_repo)
    launcher.launch(proj.id)

    assert len(settings_existed_before_create) == 1
    assert settings_existed_before_create[0] is True, (
        "settings.json must exist BEFORE adapter.create_session is called"
    )


def test_launch_settings_json_contains_hook_token(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """settings.json must contain the instance's hook_token in the hook URLs."""
    import json

    project_dir = tmp_path / "sandbox" / "token-test"
    project_dir.mkdir(parents=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="token-test", slug="token-test", path=project_dir, domain="sandbox"
        )
    )

    hooks_url = "http://127.0.0.1:8000"
    launcher = _make_launcher_with_hooks(
        fake_adapter, instances_repo, projects_repo, hooks_base_url=hooks_url
    )
    inst = launcher.launch(proj.id)

    settings_path = project_dir / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())

    # All 6 event types must be present with our token
    for ev in ("SessionStart", "Notification", "Stop", "PreToolUse", "PostToolUse", "SessionEnd"):
        assert ev in data["hooks"]
        cmd = data["hooks"][ev][0]["hooks"][0]["command"]
        assert inst.hook_token in cmd, f"hook_token must appear in command for {ev}"
        assert hooks_url in cmd, f"hooks_base_url must appear in command for {ev}"


def test_launch_settings_write_failure_marks_crashed_and_raises(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """If apply_hooks_to_settings raises, instance is marked crashed and TmuxOperationError raised.

    adapter.create_session must NOT be called.
    """
    project_dir = tmp_path / "sandbox" / "fail-settings"
    project_dir.mkdir(parents=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="fail-settings", slug="fail-settings", path=project_dir, domain="sandbox"
        )
    )

    # Monkeypatch apply_hooks_to_settings to always raise
    from claude_remote.services import claude_settings as cs_module

    original_fn = cs_module.apply_hooks_to_settings

    def _always_raises(settings_path, hook_token, base_url):
        raise OSError("simulated write failure")

    cs_module.apply_hooks_to_settings = _always_raises  # type: ignore[assignment]

    try:
        launcher = _make_launcher_with_hooks(fake_adapter, instances_repo, projects_repo)
        with pytest.raises(TmuxOperationError):
            launcher.launch(proj.id)

        # Verify adapter was NOT called
        create_calls = [c for c in fake_adapter.calls if c[0] == "create_session"]
        assert len(create_calls) == 0, "adapter.create_session must NOT be called when settings write fails"

        # Verify instance is marked crashed
        all_instances = instances_repo.list_all()
        assert len(all_instances) == 1
        assert all_instances[0].status == "crashed"
    finally:
        cs_module.apply_hooks_to_settings = original_fn  # type: ignore[assignment]


def test_launch_hook_token_in_returned_instance(
    fake_adapter: FakeTmuxAdapter,
    instances_repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """The launched Instance must expose a non-null hook_token."""
    project_dir = tmp_path / "sandbox" / "token-return-test"
    project_dir.mkdir(parents=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="token-return-test",
            slug="token-return-test",
            path=project_dir,
            domain="sandbox",
        )
    )

    launcher = _make_launcher_with_hooks(fake_adapter, instances_repo, projects_repo)
    inst = launcher.launch(proj.id)

    assert inst.hook_token is not None
    assert len(inst.hook_token) > 0
