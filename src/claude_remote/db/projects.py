"""ProjectsRepository — DB layer for the projects table.

Uses a connection_factory: Callable[[], ContextManager[sqlite3.Connection]]
so tests can inject an in-memory or temp-file DB without touching env vars.

Domain model:
  ProjectCreate — input fields (no server-generated fields)
  Project       — full record returned after create/get/list

DuplicateProjectError is raised by create() when UNIQUE(domain, slug) is
violated; routes translate it to HTTP 409.
"""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from claude_remote.services.exceptions import ProjectNotFoundError

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DuplicateProjectError(Exception):
    """Raised when a (domain, slug) pair already exists in the projects table."""

    def __init__(self, domain: str, slug: str) -> None:
        super().__init__(f"Slug '{slug}' already exists in domain '{domain}'")
        self.domain = domain
        self.slug = slug


# ---------------------------------------------------------------------------
# Domain models (Pydantic v2)
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    """Fields required to create a new project record."""

    name: str = Field(min_length=1)
    slug: str
    path: Path  # resolved absolute path (pre-validated by route layer)
    domain: str  # derived from path.parent.name by route layer


class Project(BaseModel):
    """Full project record as stored in the DB."""

    id: str
    name: str
    slug: str
    path: str  # stored as string in SQLite
    domain: str
    created_at: str  # ISO 8601 UTC
    is_stale: bool = False


# ---------------------------------------------------------------------------
# Connection factory type alias
# ---------------------------------------------------------------------------

ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class ProjectsRepository:
    """CRUD operations for the projects table.

    Args:
        connection_factory: no-arg callable returning a context manager that
            yields an open sqlite3.Connection. The context manager MUST commit
            on clean exit and rollback on exception.

    Example (production)::

        repo = ProjectsRepository(lambda: get_connection_for(settings.db_path))

    Example (tests)::

        repo = ProjectsRepository(make_connection_factory(tmp_db_path))
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    def create(self, *, project_create: ProjectCreate) -> "Project":
        """Insert a new project row and return the full Project record.

        Generates id (UUIDv4) and created_at (ISO 8601 UTC) server-side.

        Raises:
            DuplicateProjectError: when UNIQUE(domain, slug) is violated.
        """
        project_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()
        path_str = str(project_create.path)

        try:
            with self._factory() as conn:
                conn.execute(
                    """
                    INSERT INTO projects (id, slug, name, path, domain, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        project_create.slug,
                        project_create.name,
                        path_str,
                        project_create.domain,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DuplicateProjectError(project_create.domain, project_create.slug) from err

        return Project(
            id=project_id,
            name=project_create.name,
            slug=project_create.slug,
            path=path_str,
            domain=project_create.domain,
            created_at=created_at,
        )

    def list_all(self) -> list["Project"]:
        """Return all projects ordered by created_at DESC (newest first)."""
        with self._factory() as conn:
            rows = conn.execute(
                "SELECT id, slug, name, path, domain, created_at, is_stale"
                " FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_project(row) for row in rows]

    def get(self, project_id: str) -> "Project | None":
        """Return a single project by id, or None if not found."""
        with self._factory() as conn:
            row = conn.execute(
                "SELECT id, slug, name, path, domain, created_at, is_stale"
                " FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_project(row)

    def delete(self, project_id: str) -> bool:
        """Delete a project by id.

        Returns:
            True if a row was deleted, False if the id was not found.
        """
        with self._factory() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cursor.rowcount > 0

    def mark_stale(self, project_id: str) -> "Project":
        """Set is_stale=1 for the given project. Returns updated Project.

        Raises:
            ProjectNotFoundError: when no project with project_id exists.
        """
        with self._factory() as conn:
            cursor = conn.execute(
                "UPDATE projects SET is_stale = 1 WHERE id = ?", (project_id,)
            )
            if cursor.rowcount == 0:
                raise ProjectNotFoundError(project_id)
        project = self.get(project_id)
        assert project is not None  # rowcount > 0 confirmed the row exists
        return project

    def unmark_stale(self, project_id: str) -> "Project":
        """Set is_stale=0 for the given project. Returns updated Project. Idempotent.

        Raises:
            ProjectNotFoundError: when no project with project_id exists.
        """
        with self._factory() as conn:
            cursor = conn.execute(
                "UPDATE projects SET is_stale = 0 WHERE id = ?", (project_id,)
            )
            if cursor.rowcount == 0:
                raise ProjectNotFoundError(project_id)
        project = self.get(project_id)
        assert project is not None
        return project

    @staticmethod
    def _row_to_project(row: Any) -> "Project":
        return Project(
            id=row[0],
            slug=row[1],
            name=row[2],
            path=row[3],
            domain=row[4],
            created_at=row[5],
            is_stale=bool(row[6]),  # SQLite returns 0/1 — coerce to bool
        )
