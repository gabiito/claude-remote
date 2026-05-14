"""Red tests for ProjectsRepository — WU-4.

All tests use a tmp SQLite file (not env var). The ProjectsRepository
receives a connection_factory pointing at the tmp DB.

Fixture strategy:
  - `repo_db` applies migrations to a fresh tmp DB and returns a
    ProjectsRepository pointing at it.
  - Each test creates its own project dirs inside `tmp_projects_root`.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import (
    DuplicateProjectError,
    Project,
    ProjectCreate,
    ProjectsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_connection_factory(db_path: Path):
    """Return a no-arg callable that yields a sqlite3 connection."""

    @contextmanager
    def factory():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return factory


def make_project_create(
    *,
    name: str = "My Project",
    slug: str = "my-project",
    path: Path,
    domain: str = "sandbox",
) -> ProjectCreate:
    return ProjectCreate(name=name, slug=slug, path=path, domain=domain)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def project_path(tmp_path: Path) -> Path:
    """A fake project directory at <tmp>/sandbox/my-project/."""
    p = tmp_path / "sandbox" / "my-project"
    p.mkdir(parents=True)
    return p


@pytest.fixture()
def repo(tmp_db_path: Path) -> ProjectsRepository:
    """Apply migrations and return a fresh ProjectsRepository."""
    apply_migrations(tmp_db_path, MIGRATIONS_DIR)
    factory = make_connection_factory(tmp_db_path)
    return ProjectsRepository(connection_factory=factory)


# ---------------------------------------------------------------------------
# WU-4: create — happy path
# ---------------------------------------------------------------------------


def test_create_returns_project_with_all_fields(
    repo: ProjectsRepository, project_path: Path
) -> None:
    pc = make_project_create(path=project_path)
    result = repo.create(project_create=pc)

    assert isinstance(result, Project)
    assert result.name == "My Project"
    assert result.slug == "my-project"
    assert result.domain == "sandbox"
    assert result.path == str(project_path)
    assert result.id  # non-empty
    assert result.created_at  # non-empty


def test_create_id_is_uuid_format(repo: ProjectsRepository, project_path: Path) -> None:
    import re

    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    result = repo.create(project_create=make_project_create(path=project_path))
    assert uuid_re.match(result.id), f"ID {result.id!r} is not a valid UUIDv4"


def test_create_created_at_is_iso8601(repo: ProjectsRepository, project_path: Path) -> None:
    from datetime import datetime

    result = repo.create(project_create=make_project_create(path=project_path))
    # Must parse without error
    dt = datetime.fromisoformat(result.created_at)
    assert dt.tzinfo is not None  # Must be timezone-aware (UTC)


# ---------------------------------------------------------------------------
# WU-4: create — duplicate slug raises DuplicateProjectError
# ---------------------------------------------------------------------------


def test_create_duplicate_domain_slug_raises(
    repo: ProjectsRepository, tmp_path: Path
) -> None:
    path_a = tmp_path / "sandbox" / "proj-a"
    path_a.mkdir(parents=True)
    path_b = tmp_path / "sandbox" / "proj-b"
    path_b.mkdir(parents=True)

    pc_a = make_project_create(slug="shared-slug", path=path_a)
    pc_b = ProjectCreate(
        name="Another",
        slug="shared-slug",
        path=path_b,
        domain="sandbox",
    )

    repo.create(project_create=pc_a)

    with pytest.raises(DuplicateProjectError) as exc_info:
        repo.create(project_create=pc_b)

    assert exc_info.value.domain == "sandbox"
    assert exc_info.value.slug == "shared-slug"


def test_create_same_slug_different_domain_ok(
    repo: ProjectsRepository, tmp_path: Path
) -> None:
    """Same slug under different domains should not conflict."""
    path_a = tmp_path / "domain-a" / "project"
    path_a.mkdir(parents=True)
    path_b = tmp_path / "domain-b" / "project"
    path_b.mkdir(parents=True)

    repo.create(project_create=ProjectCreate(name="P", slug="slug", path=path_a, domain="domain-a"))
    result = repo.create(
        project_create=ProjectCreate(name="P", slug="slug", path=path_b, domain="domain-b")
    )
    assert result.domain == "domain-b"


# ---------------------------------------------------------------------------
# WU-4: list_all — ordering and empty
# ---------------------------------------------------------------------------


def test_list_all_empty(repo: ProjectsRepository) -> None:
    assert repo.list_all() == []


def test_list_all_order_created_at_desc(repo: ProjectsRepository, tmp_path: Path) -> None:
    """Projects created later must appear first (DESC)."""
    path_a = tmp_path / "domain" / "proj-a"
    path_a.mkdir(parents=True)
    path_b = tmp_path / "domain" / "proj-b"
    path_b.mkdir(parents=True)

    first = repo.create(
        project_create=ProjectCreate(name="A", slug="a", path=path_a, domain="domain")
    )
    time.sleep(0.01)  # ensure distinct timestamps
    second = repo.create(
        project_create=ProjectCreate(name="B", slug="b", path=path_b, domain="domain")
    )

    results = repo.list_all()
    assert len(results) == 2
    assert results[0].id == second.id  # newer first
    assert results[1].id == first.id


# ---------------------------------------------------------------------------
# WU-4: get
# ---------------------------------------------------------------------------


def test_get_found(repo: ProjectsRepository, project_path: Path) -> None:
    created = repo.create(project_create=make_project_create(path=project_path))
    found = repo.get(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.name == created.name


def test_get_not_found(repo: ProjectsRepository) -> None:
    result = repo.get("nonexistent-id")
    assert result is None


# ---------------------------------------------------------------------------
# WU-4: delete
# ---------------------------------------------------------------------------


def test_delete_found_returns_true(repo: ProjectsRepository, project_path: Path) -> None:
    created = repo.create(project_create=make_project_create(path=project_path))
    result = repo.delete(created.id)
    assert result is True


def test_delete_removes_row(repo: ProjectsRepository, project_path: Path) -> None:
    created = repo.create(project_create=make_project_create(path=project_path))
    repo.delete(created.id)
    assert repo.get(created.id) is None


def test_delete_not_found_returns_false(repo: ProjectsRepository) -> None:
    result = repo.delete("nonexistent-id")
    assert result is False
