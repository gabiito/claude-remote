"""NotificationsRepository — DB layer for the singleton notification_preferences row.

Singleton invariant: every read/write uses WHERE id = 1. The DB-level
CHECK(id = 1) constraint enforces this against any caller bug.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class NotificationPreferences(BaseModel):
    """Mirror of the notification_preferences table row.

    INTEGER columns are exposed as bool; the repo handles int↔bool coercion
    at the DB boundary so callers always work with typed Python values.
    """

    notify_on_notification: bool
    notify_on_stop: bool
    notify_on_session_end: bool
    notify_on_session_start: bool
    notify_on_pre_tool_use: bool
    notify_on_post_tool_use: bool
    quiet_hours_start: str | None
    quiet_hours_end: str | None
    ntfy_topic: str
    updated_at: str


ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]

_COLUMNS = (
    "notify_on_notification",
    "notify_on_stop",
    "notify_on_session_end",
    "notify_on_session_start",
    "notify_on_pre_tool_use",
    "notify_on_post_tool_use",
    "quiet_hours_start",
    "quiet_hours_end",
    "ntfy_topic",
    "updated_at",
)
_UPDATABLE: frozenset[str] = frozenset(_COLUMNS)


class NotificationsRepository:
    """CRUD operations for the singleton notification_preferences table.

    Args:
        connection_factory: no-arg callable returning a context manager that
            yields an open sqlite3.Connection. The context manager MUST commit
            on clean exit and rollback on exception.

    Example (production)::

        repo = NotificationsRepository(lambda: get_connection_for(settings.db_path))

    Example (tests)::

        repo = NotificationsRepository(lambda: get_connection_for(tmp_db_path))
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    def get(self) -> NotificationPreferences:
        """Return the singleton preference row (id=1).

        Raises:
            RuntimeError: when the singleton row is missing (migration 0006 not applied).
        """
        with self._factory() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM notification_preferences WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError(
                "notification_preferences row missing; check migration 0006 was applied"
            )
        return self._row_to_prefs(row)

    def update(self, **fields: Any) -> NotificationPreferences:
        """Update the specified fields on the singleton row and return the updated model.

        Only the supplied field names are written; all other columns are unchanged.
        updated_at is always stamped with the current UTC time.

        Args:
            **fields: keyword arguments matching column names in _UPDATABLE.

        Raises:
            ValueError: when any key in fields is not a known updatable column.
        """
        if not fields:
            return self.get()

        unknown = set(fields.keys()) - _UPDATABLE
        if unknown:
            raise ValueError(f"Unknown preference fields: {sorted(unknown)}")

        # Always stamp updated_at
        fields.setdefault("updated_at", datetime.now(UTC).isoformat())

        # Coerce bool → int for SQLite
        normalised: dict[str, Any] = {
            k: (1 if v is True else 0 if v is False else v)
            for k, v in fields.items()
        }

        set_clause = ", ".join(f"{k} = ?" for k in normalised)
        values = tuple(normalised.values())

        with self._factory() as conn:
            conn.execute(
                f"UPDATE notification_preferences SET {set_clause} WHERE id = 1",
                values,
            )
        return self.get()

    @staticmethod
    def _row_to_prefs(row: Any) -> NotificationPreferences:
        return NotificationPreferences(
            notify_on_notification=bool(row[0]),
            notify_on_stop=bool(row[1]),
            notify_on_session_end=bool(row[2]),
            notify_on_session_start=bool(row[3]),
            notify_on_pre_tool_use=bool(row[4]),
            notify_on_post_tool_use=bool(row[5]),
            quiet_hours_start=row[6],
            quiet_hours_end=row[7],
            ntfy_topic=row[8],
            updated_at=row[9],
        )
