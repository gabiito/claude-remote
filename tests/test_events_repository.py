"""Red tests for WU-1 — EventsRepository CRUD and constraints.

All tests use a tmp SQLite DB with all migrations applied.
The connection factory goes through get_connection_for so PRAGMA foreign_keys
is ON — cascade deletes are tested explicitly.

Fixture strategy:
  - ``db_path`` — fresh tmp DB file per test.
  - ``repo``    — EventsRepository wired to the migrated DB.
  - ``proj_id`` — a project row inserted via ProjectsRepository.
  - ``inst_id`` — an instance row inserted via InstancesRepository.
"""

import re
import sqlite3
import time
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import Event, EventsRepository
from claude_remote.db.instances import InstancesRepository
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


def _make_instance(
    instances_repo: InstancesRepository, project_id: str, *, suffix: str = "aa"
) -> str:
    """Insert an instance row and return its id."""
    inst = instances_repo.create(
        project_id=project_id,
        tmux_session_name=f"claude-remote-proj-{suffix}000001",
    )
    return inst.id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    apply_migrations(path, MIGRATIONS_DIR)
    return path


@pytest.fixture()
def repo(db_path: Path) -> EventsRepository:
    return EventsRepository(connection_factory=_make_factory(db_path))


@pytest.fixture()
def projects_repo(db_path: Path) -> ProjectsRepository:
    return ProjectsRepository(connection_factory=_make_factory(db_path))


@pytest.fixture()
def instances_repo(db_path: Path) -> InstancesRepository:
    return InstancesRepository(connection_factory=_make_factory(db_path))


@pytest.fixture()
def proj_id(projects_repo: ProjectsRepository, tmp_path: Path) -> str:
    return _make_project(projects_repo, tmp_path)


@pytest.fixture()
def inst_id(instances_repo: InstancesRepository, proj_id: str) -> str:
    return _make_instance(instances_repo, proj_id)


# ---------------------------------------------------------------------------
# create — happy path
# ---------------------------------------------------------------------------


def test_create_returns_event_with_uuid_id(
    repo: EventsRepository, proj_id: str, inst_id: str
) -> None:
    event = repo.create(
        instance_id=inst_id,
        project_id=proj_id,
        event_type="Notification",
        payload='{"text": "hello"}',
    )
    assert UUID_RE.match(event.id), f"event.id {event.id!r} is not UUIDv4"


def test_create_sets_iso8601_received_at(
    repo: EventsRepository, proj_id: str, inst_id: str
) -> None:
    event = repo.create(
        instance_id=inst_id,
        project_id=proj_id,
        event_type="SessionStart",
        payload="{}",
    )
    # Basic ISO 8601 check: must contain a 'T' date-time separator
    assert "T" in event.received_at, f"received_at {event.received_at!r} is not ISO 8601"


def test_create_returns_correct_fields(
    repo: EventsRepository, proj_id: str, inst_id: str
) -> None:
    payload = '{"tool": "Read"}'
    event = repo.create(
        instance_id=inst_id,
        project_id=proj_id,
        event_type="PreToolUse",
        payload=payload,
    )
    assert isinstance(event, Event)
    assert event.instance_id == inst_id
    assert event.project_id == proj_id
    assert event.event_type == "PreToolUse"
    assert event.payload == payload


def test_create_with_none_instance_id(
    repo: EventsRepository, proj_id: str
) -> None:
    """Events may arrive for unknown instances — both FK columns are nullable."""
    event = repo.create(
        instance_id=None,
        project_id=proj_id,
        event_type="Notification",
        payload="{}",
    )
    assert event.instance_id is None
    assert event.project_id == proj_id


def test_create_with_none_project_id(
    repo: EventsRepository, inst_id: str, proj_id: str
) -> None:
    """project_id is also nullable."""
    event = repo.create(
        instance_id=inst_id,
        project_id=None,
        event_type="Stop",
        payload="{}",
    )
    assert event.project_id is None


def test_create_check_constraint_rejects_invalid_event_type(
    repo: EventsRepository, proj_id: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(
            instance_id=None,
            project_id=proj_id,
            event_type="InvalidType",  # type: ignore[arg-type]
            payload="{}",
        )


# ---------------------------------------------------------------------------
# list_recent — ordering and limit
# ---------------------------------------------------------------------------


def test_list_recent_returns_desc_order(
    repo: EventsRepository, proj_id: str
) -> None:
    first = repo.create(
        instance_id=None, project_id=proj_id, event_type="SessionStart", payload="{}"
    )
    time.sleep(0.01)
    second = repo.create(
        instance_id=None, project_id=proj_id, event_type="Notification", payload="{}"
    )

    results = repo.list_recent(limit=50)
    assert len(results) >= 2
    ids = [e.id for e in results]
    assert ids.index(second.id) < ids.index(first.id), "Newer event must appear first"


def test_list_recent_respects_limit(
    repo: EventsRepository, proj_id: str
) -> None:
    for i in range(10):
        repo.create(
            instance_id=None, project_id=proj_id, event_type="Notification", payload="{}"
        )
        if i < 9:
            time.sleep(0.005)
    results = repo.list_recent(limit=5)
    assert len(results) == 5


def test_list_recent_empty_returns_empty(repo: EventsRepository) -> None:
    assert repo.list_recent() == []


# ---------------------------------------------------------------------------
# list_for_project — filtering
# ---------------------------------------------------------------------------


def test_list_for_project_filters_by_project(
    repo: EventsRepository,
    projects_repo: ProjectsRepository,
    tmp_path: Path,
    proj_id: str,
) -> None:
    proj_b_id = _make_project(projects_repo, tmp_path, slug="proj-b")

    ev_a = repo.create(
        instance_id=None, project_id=proj_id, event_type="Notification", payload="{}"
    )
    repo.create(
        instance_id=None, project_id=proj_b_id, event_type="Notification", payload="{}"
    )

    results = repo.list_for_project(proj_id)
    assert len(results) == 1
    assert results[0].id == ev_a.id


def test_list_for_project_ordered_desc(
    repo: EventsRepository, proj_id: str
) -> None:
    first = repo.create(
        instance_id=None, project_id=proj_id, event_type="SessionStart", payload="{}"
    )
    time.sleep(0.01)
    second = repo.create(
        instance_id=None, project_id=proj_id, event_type="Notification", payload="{}"
    )

    results = repo.list_for_project(proj_id)
    ids = [e.id for e in results]
    assert ids.index(second.id) < ids.index(first.id)


def test_list_for_project_empty_when_none(
    repo: EventsRepository, proj_id: str
) -> None:
    assert repo.list_for_project(proj_id) == []


# ---------------------------------------------------------------------------
# list_for_instance — filtering
# ---------------------------------------------------------------------------


def test_list_for_instance_filters_by_instance(
    repo: EventsRepository,
    instances_repo: InstancesRepository,
    proj_id: str,
    inst_id: str,
) -> None:
    inst_b_id = _make_instance(instances_repo, proj_id, suffix="bb")

    ev_a = repo.create(
        instance_id=inst_id, project_id=proj_id, event_type="Notification", payload="{}"
    )
    repo.create(
        instance_id=inst_b_id, project_id=proj_id, event_type="Notification", payload="{}"
    )

    results = repo.list_for_instance(inst_id)
    assert len(results) == 1
    assert results[0].id == ev_a.id


def test_list_for_instance_empty_when_none(
    repo: EventsRepository, inst_id: str
) -> None:
    assert repo.list_for_instance(inst_id) == []


# ---------------------------------------------------------------------------
# CASCADE deletes — FK integrity
# ---------------------------------------------------------------------------


def test_cascade_delete_via_project(
    repo: EventsRepository,
    projects_repo: ProjectsRepository,
    proj_id: str,
    db_path: Path,
) -> None:
    """Deleting a project must cascade-delete its events."""
    repo.create(instance_id=None, project_id=proj_id, event_type="Notification", payload="{}")
    projects_repo.delete(proj_id)

    with get_connection_for(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id = ?", (proj_id,)
        ).fetchone()[0]
    assert count == 0, "CASCADE DELETE must remove event rows when project is deleted"


def test_cascade_delete_via_instance(
    repo: EventsRepository,
    instances_repo: InstancesRepository,
    inst_id: str,
    db_path: Path,
) -> None:
    """Deleting an instance must cascade-delete its events."""
    repo.create(instance_id=inst_id, project_id=None, event_type="SessionEnd", payload="{}")
    instances_repo.delete(inst_id)

    with get_connection_for(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE instance_id = ?", (inst_id,)
        ).fetchone()[0]
    assert count == 0, "CASCADE DELETE must remove event rows when instance is deleted"
