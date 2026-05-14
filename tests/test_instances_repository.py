"""Red tests for WU-2 — InstancesRepository CRUD and constraints.

All tests use a tmp SQLite DB with both migrations applied (0001 + 0002).
The connection factory goes through get_connection_for so PRAGMA foreign_keys
is ON — this also acts as a living proof that WU-1's pragma fix is effective
(test_cascade_delete_enforces_fk proves it end-to-end).

Fixture strategy:
  - ``db_path`` — fresh tmp DB file per test.
  - ``repo``    — InstancesRepository wired to the migrated DB.
  - ``proj_id`` — a project row inserted via ProjectsRepository (needed for FK).
"""

import re
import sqlite3
import time
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _make_factory(db_path: Path):
    return lambda: get_connection_for(db_path)


def _make_project(projects_repo: ProjectsRepository, tmp_path: Path, *, slug: str = "proj") -> str:
    """Insert a project row and return its id."""
    p = tmp_path / "sandbox" / slug
    p.mkdir(parents=True, exist_ok=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(name=slug, slug=slug, path=p, domain="sandbox")
    )
    return proj.id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    apply_migrations(path, MIGRATIONS_DIR)
    return path


@pytest.fixture()
def repo(db_path: Path) -> InstancesRepository:
    return InstancesRepository(connection_factory=_make_factory(db_path))


@pytest.fixture()
def projects_repo(db_path: Path) -> ProjectsRepository:
    return ProjectsRepository(connection_factory=_make_factory(db_path))


@pytest.fixture()
def proj_id(projects_repo: ProjectsRepository, tmp_path: Path) -> str:
    return _make_project(projects_repo, tmp_path)


# ---------------------------------------------------------------------------
# create / get round-trip
# ---------------------------------------------------------------------------


def test_create_and_get_roundtrip(repo: InstancesRepository, proj_id: str) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-abc12345")
    fetched = repo.get(inst.id)
    assert fetched is not None
    assert fetched.id == inst.id
    assert fetched.project_id == proj_id
    assert fetched.tmux_session_name == "claude-remote-proj-abc12345"
    assert fetched.status == "starting"
    assert fetched.pane_pid is None
    assert fetched.stopped_at is None
    assert fetched.created_at


def test_create_generates_uuid(repo: InstancesRepository, proj_id: str) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-00000001")
    assert UUID_RE.match(inst.id), f"ID {inst.id!r} is not UUIDv4"


def test_get_nonexistent_returns_none(repo: InstancesRepository) -> None:
    assert repo.get("nonexistent-id") is None


# ---------------------------------------------------------------------------
# list_all ordering
# ---------------------------------------------------------------------------


def test_list_all_empty(repo: InstancesRepository) -> None:
    assert repo.list_all() == []


def test_list_all_order_newest_first(
    repo: InstancesRepository, proj_id: str
) -> None:
    first = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-aaaaaaaa")
    time.sleep(0.01)
    second = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-bbbbbbbb")

    results = repo.list_all()
    assert len(results) == 2
    assert results[0].id == second.id
    assert results[1].id == first.id


# ---------------------------------------------------------------------------
# list_active_for_project
# ---------------------------------------------------------------------------


def test_list_active_for_project_includes_starting_and_running(
    repo: InstancesRepository, proj_id: str
) -> None:
    inst_starting = repo.create(
        project_id=proj_id, tmux_session_name="claude-remote-proj-st000001", status="starting"
    )
    inst_running = repo.create(
        project_id=proj_id, tmux_session_name="claude-remote-proj-ru000001", status="running"
    )
    repo.create(
        project_id=proj_id, tmux_session_name="claude-remote-proj-st000002", status="stopped"
    )
    repo.create(
        project_id=proj_id, tmux_session_name="claude-remote-proj-cr000001", status="crashed"
    )

    active = repo.list_active_for_project(proj_id)
    active_ids = {i.id for i in active}
    assert inst_starting.id in active_ids
    assert inst_running.id in active_ids
    assert len(active) == 2


def test_list_active_for_project_empty_when_all_terminal(
    repo: InstancesRepository, proj_id: str
) -> None:
    repo.create(
        project_id=proj_id, tmux_session_name="claude-remote-proj-st000003", status="stopped"
    )
    assert repo.list_active_for_project(proj_id) == []


# ---------------------------------------------------------------------------
# update_status — COALESCE / partial update
# ---------------------------------------------------------------------------


def test_update_status_changes_status(repo: InstancesRepository, proj_id: str) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-up000001")
    updated = repo.update_status(inst.id, status="running", pane_pid=9999)
    assert updated.status == "running"
    assert updated.pane_pid == 9999
    assert updated.stopped_at is None


def test_update_status_coalesces_pane_pid(repo: InstancesRepository, proj_id: str) -> None:
    """Passing pane_pid=None should PRESERVE the existing pane_pid (COALESCE semantics)."""
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-co000001")
    repo.update_status(inst.id, status="running", pane_pid=1234)
    # Now update only status, leaving pane_pid as None (should preserve 1234)
    updated = repo.update_status(inst.id, status="crashed", stopped_at="2026-01-01T00:00:00+00:00")
    assert updated.pane_pid == 1234  # preserved via COALESCE


def test_update_status_coalesces_stopped_at(repo: InstancesRepository, proj_id: str) -> None:
    """Passing stopped_at=None should PRESERVE the existing stopped_at."""
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-co000002")
    ts = "2026-01-01T00:00:00+00:00"
    repo.update_status(inst.id, status="stopped", stopped_at=ts)
    # Update again without stopped_at — should remain
    updated = repo.update_status(inst.id, status="stopped")
    assert updated.stopped_at == ts


def test_update_status_returns_updated_instance(repo: InstancesRepository, proj_id: str) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-ret000001")
    updated = repo.update_status(inst.id, status="running")
    assert isinstance(updated, Instance)
    assert updated.id == inst.id


# ---------------------------------------------------------------------------
# mark_stopped
# ---------------------------------------------------------------------------


def test_mark_stopped_sets_status_and_stopped_at(
    repo: InstancesRepository, proj_id: str
) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-ms000001")
    repo.update_status(inst.id, status="running")
    stopped = repo.mark_stopped(inst.id)
    assert stopped.status == "stopped"
    assert stopped.stopped_at is not None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_existing_returns_true(repo: InstancesRepository, proj_id: str) -> None:
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-del000001")
    assert repo.delete(inst.id) is True
    assert repo.get(inst.id) is None


def test_delete_nonexistent_returns_false(repo: InstancesRepository) -> None:
    assert repo.delete("nonexistent-id") is False


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_unique_tmux_session_name_raises_integrity_error(
    repo: InstancesRepository, proj_id: str
) -> None:
    repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-dup00001")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(project_id=proj_id, tmux_session_name="claude-remote-proj-dup00001")


def test_status_check_constraint_rejects_invalid_status(
    repo: InstancesRepository, proj_id: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(
            project_id=proj_id,
            tmux_session_name="claude-remote-proj-bad00001",
            status="unknown",
        )


def test_migration_idempotent(db_path: Path) -> None:
    """Applying migrations twice results in exactly one schema_migrations row per file."""
    apply_migrations(db_path, MIGRATIONS_DIR)  # second application
    with get_connection_for(db_path) as conn:
        rows = conn.execute(
            "SELECT filename FROM schema_migrations WHERE filename = '0002_create_instances.sql'"
        ).fetchall()
    assert len(rows) == 1, "Migration must appear exactly once in schema_migrations"


# ---------------------------------------------------------------------------
# list_by_project — all statuses, scoped to project
# ---------------------------------------------------------------------------


def test_list_by_project_returns_all_statuses_for_project(
    repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """list_by_project returns ALL instances for the given project regardless of status."""
    proj_a_id = _make_project(projects_repo, tmp_path, slug="proj-a")
    proj_b_id = _make_project(projects_repo, tmp_path, slug="proj-b")

    # Three instances for project A — one of each terminal/active status
    inst_running = repo.create(
        project_id=proj_a_id,
        tmux_session_name="claude-remote-proj-a-ru000001",
        status="running",
    )
    time.sleep(0.01)
    inst_stopped = repo.create(
        project_id=proj_a_id,
        tmux_session_name="claude-remote-proj-a-st000001",
        status="stopped",
    )
    time.sleep(0.01)
    inst_crashed = repo.create(
        project_id=proj_a_id,
        tmux_session_name="claude-remote-proj-a-cr000001",
        status="crashed",
    )

    # One instance for project B — must NOT appear in results for A
    repo.create(
        project_id=proj_b_id,
        tmux_session_name="claude-remote-proj-b-ru000001",
        status="running",
    )

    results = repo.list_by_project(proj_a_id)

    assert len(results) == 3
    result_ids = {i.id for i in results}
    assert inst_running.id in result_ids
    assert inst_stopped.id in result_ids
    assert inst_crashed.id in result_ids

    # All results belong to project A
    for inst in results:
        assert inst.project_id == proj_a_id

    # Statuses present: running, stopped, crashed (all three)
    result_statuses = {i.status for i in results}
    assert result_statuses == {"running", "stopped", "crashed"}

    # Ordered newest first (created_at DESC)
    assert results[0].id == inst_crashed.id
    assert results[1].id == inst_stopped.id
    assert results[2].id == inst_running.id


def test_list_by_project_empty_when_project_has_no_instances(
    repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
) -> None:
    """list_by_project returns [] when the project exists but has no instances."""
    proj_id = _make_project(projects_repo, tmp_path, slug="empty-proj")
    assert repo.list_by_project(proj_id) == []


# ---------------------------------------------------------------------------
# FK cascade — WU-1 pragma fix is alive (S1.3)
# ---------------------------------------------------------------------------


def test_cascade_delete_enforces_fk(
    repo: InstancesRepository,
    projects_repo: ProjectsRepository,
    db_path: Path,
    tmp_path: Path,
) -> None:
    """Deleting a project must cascade-delete its instances (PRAGMA foreign_keys = ON)."""
    proj_id = _make_project(projects_repo, tmp_path, slug="cascade-proj")
    repo.create(project_id=proj_id, tmux_session_name="claude-remote-cascade-proj-00000001")

    # Delete project via repository
    projects_repo.delete(proj_id)

    # Instance must be gone (cascade delete)
    with get_connection_for(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE project_id = ?", (proj_id,)
        ).fetchone()[0]
    assert count == 0, "CASCADE DELETE must remove instance rows when project is deleted"
