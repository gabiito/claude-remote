"""EventsRepository — DB layer for the events table.

Uses a connection_factory: Callable[[], ContextManager[sqlite3.Connection]]
so tests can inject an in-memory or temp-file DB without touching env vars.
All connections opened through the shared factory have PRAGMA foreign_keys = ON
(guaranteed by db/connection.py::get_connection_for).

Domain models:
  Event — full record returned after create/list operations.

Notable choices:
  - Both instance_id and project_id are nullable: events may arrive for
    unknown instances during race conditions (FK columns are nullable per spec).
  - event_type is enforced by a DB CHECK constraint; the Literal type mirrors it.
  - received_at and id are server-generated (UUID4 + ISO 8601 UTC).
"""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EventType = Literal[
    "SessionStart",
    "Notification",
    "Stop",
    "PreToolUse",
    "PostToolUse",
    "SessionEnd",
]

EVENT_TYPES: tuple[str, ...] = (
    "SessionStart",
    "Notification",
    "Stop",
    "PreToolUse",
    "PostToolUse",
    "SessionEnd",
)

# ---------------------------------------------------------------------------
# Domain models (Pydantic v2)
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """Full event record as stored in the DB."""

    id: str
    instance_id: str | None
    project_id: str | None
    event_type: str  # CHECK constraint at DB layer; Literal used in create signature
    payload: str  # raw JSON string
    received_at: str  # ISO 8601 UTC


# ---------------------------------------------------------------------------
# Connection factory type alias (mirrors existing repo pattern)
# ---------------------------------------------------------------------------

ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class EventsRepository:
    """CRUD operations for the events table.

    Args:
        connection_factory: no-arg callable returning a context manager that
            yields an open sqlite3.Connection with FK enforcement.
            The context manager MUST commit on clean exit and rollback on error.

    Example (production)::

        repo = EventsRepository(lambda: get_connection_for(settings.db_path))

    Example (tests)::

        repo = EventsRepository(lambda: get_connection_for(tmp_db_path))
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    # ------------------------------------------------------------------
    # write operations
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        instance_id: str | None,
        project_id: str | None,
        event_type: str,
        payload: str,
    ) -> Event:
        """Insert a new event row and return the full Event record.

        Generates ``id`` (UUIDv4) and ``received_at`` (ISO 8601 UTC) server-side.

        Args:
            instance_id: FK reference to instances.id (nullable).
            project_id: FK reference to projects.id (nullable).
            event_type: one of the 6 accepted types (enforced by DB CHECK).
            payload: raw JSON string of the event body.

        Raises:
            sqlite3.IntegrityError: when the event_type CHECK constraint is
                violated or an FK reference is invalid.
        """
        event_id = str(uuid.uuid4())
        received_at = datetime.now(UTC).isoformat()

        with self._factory() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (id, instance_id, project_id, event_type, payload, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, instance_id, project_id, event_type, payload, received_at),
            )

        return Event(
            id=event_id,
            instance_id=instance_id,
            project_id=project_id,
            event_type=event_type,
            payload=payload,
            received_at=received_at,
        )

    # ------------------------------------------------------------------
    # read operations
    # ------------------------------------------------------------------

    def list_recent(self, limit: int = 50) -> list[Event]:
        """Return the most recent events across all projects/instances.

        Ordered by received_at DESC (newest first), limited to ``limit`` rows.
        """
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, instance_id, project_id, event_type, payload, received_at
                FROM events
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_for_project(self, project_id: str, limit: int = 50) -> list[Event]:
        """Return events for a specific project, ordered received_at DESC."""
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, instance_id, project_id, event_type, payload, received_at
                FROM events
                WHERE project_id = ?
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_for_instance(self, instance_id: str, limit: int = 50) -> list[Event]:
        """Return events for a specific instance, ordered received_at DESC."""
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, instance_id, project_id, event_type, payload, received_at
                FROM events
                WHERE instance_id = ?
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (instance_id, limit),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: Any) -> Event:
        return Event(
            id=row[0],
            instance_id=row[1],
            project_id=row[2],
            event_type=row[3],
            payload=row[4],
            received_at=row[5],
        )
