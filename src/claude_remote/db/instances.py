"""InstancesRepository — DB layer for the instances table.

Uses a connection_factory: Callable[[], ContextManager[sqlite3.Connection]]
so tests can inject an in-memory or temp-file DB without touching env vars.
All connections opened through the shared factory have PRAGMA foreign_keys = ON
(guaranteed by db/connection.py::get_connection_for).

Domain models:
  Instance — full record returned after create/get/list/update operations.

Notable choices:
  - update_status uses COALESCE so callers only specify columns they want to
    change; None means "leave the current value unchanged" for pane_pid and
    stopped_at.
  - mark_stopped is a thin convenience wrapper for the running → stopped
    transition (most common terminal transition via explicit POST /stop).
  - list_active_for_project drives the launch 409 check — only 'starting' and
    'running' rows are returned (ACTIVE_STATUSES).
"""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = ("starting", "running")
TERMINAL_STATUSES = ("stopped", "crashed")

# ---------------------------------------------------------------------------
# Domain models (Pydantic v2)
# ---------------------------------------------------------------------------


class Instance(BaseModel):
    """Full instance record as stored in the DB."""

    id: str
    project_id: str
    tmux_session_name: str
    pane_pid: int | None
    status: str  # CHECK constraint at DB layer; validated on write
    created_at: str  # ISO 8601 UTC
    stopped_at: str | None  # ISO 8601 UTC; None when status ∈ {starting, running}


# ---------------------------------------------------------------------------
# Connection factory type alias (mirrors projects.py convention)
# ---------------------------------------------------------------------------

ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class InstancesRepository:
    """CRUD operations for the instances table.

    Args:
        connection_factory: no-arg callable returning a context manager that
            yields an open sqlite3.Connection with FK enforcement.
            The context manager MUST commit on clean exit and rollback on error.

    Example (production)::

        repo = InstancesRepository(lambda: get_connection_for(settings.db_path))

    Example (tests)::

        repo = InstancesRepository(lambda: get_connection_for(tmp_db_path))
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    # ------------------------------------------------------------------
    # write operations
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        project_id: str,
        tmux_session_name: str,
        status: str = "starting",
    ) -> Instance:
        """Insert a new instance row and return the full Instance record.

        Generates ``id`` (UUIDv4) and ``created_at`` (ISO 8601 UTC) server-side.

        Args:
            project_id: FK reference to an existing projects.id row.
            tmux_session_name: globally unique tmux session name.
                Format: ``claude-remote-{slug}-{8-hex-chars}``.
            status: initial status; defaults to ``'starting'``.

        Raises:
            sqlite3.IntegrityError: when UNIQUE(tmux_session_name) or the
                status CHECK constraint is violated, or project_id is invalid.
        """
        instance_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()

        with self._factory() as conn:
            conn.execute(
                """
                INSERT INTO instances
                    (id, project_id, tmux_session_name, pane_pid, status, created_at, stopped_at)
                VALUES (?, ?, ?, NULL, ?, ?, NULL)
                """,
                (instance_id, project_id, tmux_session_name, status, created_at),
            )

        return Instance(
            id=instance_id,
            project_id=project_id,
            tmux_session_name=tmux_session_name,
            pane_pid=None,
            status=status,
            created_at=created_at,
            stopped_at=None,
        )

    def update_status(
        self,
        instance_id: str,
        *,
        status: str,
        pane_pid: int | None = None,
        stopped_at: str | None = None,
    ) -> Instance:
        """Update status (and optionally pane_pid / stopped_at) for an instance.

        COALESCE semantics: passing ``None`` for ``pane_pid`` or ``stopped_at``
        preserves the current value in the DB row.  To explicitly set a column
        to NULL, you would need a different sentinel; for this slice ``None``
        always means "preserve existing".

        Args:
            instance_id: id of the row to update.
            status: new status value.
            pane_pid: if not None, overwrite pane_pid; if None, leave unchanged.
            stopped_at: if not None, overwrite stopped_at; if None, leave unchanged.

        Returns:
            The updated Instance record (re-fetched from DB).
        """
        with self._factory() as conn:
            conn.execute(
                """
                UPDATE instances
                SET status     = ?,
                    pane_pid   = COALESCE(?, pane_pid),
                    stopped_at = COALESCE(?, stopped_at)
                WHERE id = ?
                """,
                (status, pane_pid, stopped_at, instance_id),
            )

        result = self.get(instance_id)
        if result is None:
            raise ValueError(f"Instance '{instance_id}' not found after update")
        return result

    def mark_stopped(self, instance_id: str) -> Instance:
        """Convenience: set status='stopped' and stopped_at=now()."""
        return self.update_status(
            instance_id,
            status="stopped",
            stopped_at=datetime.now(UTC).isoformat(),
        )

    def delete(self, instance_id: str) -> bool:
        """Delete an instance row.

        Returns:
            True if a row was deleted, False if the id was not found.
        """
        with self._factory() as conn:
            cursor = conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # read operations
    # ------------------------------------------------------------------

    def get(self, instance_id: str) -> Instance | None:
        """Return a single instance by id, or None if not found."""
        with self._factory() as conn:
            row = conn.execute(
                """
                SELECT id, project_id, tmux_session_name, pane_pid,
                       status, created_at, stopped_at
                FROM instances
                WHERE id = ?
                """,
                (instance_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_instance(row)

    def list_all(self) -> list[Instance]:
        """Return all instances ordered by created_at DESC (newest first)."""
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, tmux_session_name, pane_pid,
                       status, created_at, stopped_at
                FROM instances
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._row_to_instance(row) for row in rows]

    def list_active_for_project(self, project_id: str) -> list[Instance]:
        """Return instances for a project with status in ('starting', 'running').

        Used by TmuxLauncher to check for active instances before launch (409
        guard) and to drive pre-launch reconciliation.
        """
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, tmux_session_name, pane_pid,
                       status, created_at, stopped_at
                FROM instances
                WHERE project_id = ? AND status IN ('starting', 'running')
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_instance(row) for row in rows]

    def list_by_project(self, project_id: str) -> list[Instance]:
        """Return all instances for the given project, regardless of status.

        Covers the full lifecycle history (starting, running, stopped, crashed).
        Ordered by created_at DESC (newest first), consistent with list_all.
        """
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, tmux_session_name, pane_pid,
                       status, created_at, stopped_at
                FROM instances
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_instance(row) for row in rows]

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_instance(row: Any) -> Instance:
        return Instance(
            id=row[0],
            project_id=row[1],
            tmux_session_name=row[2],
            pane_pid=row[3],
            status=row[4],
            created_at=row[5],
            stopped_at=row[6],
        )
