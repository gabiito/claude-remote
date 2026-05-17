"""AppSettingsRepository — singleton row holding runtime-configurable settings.

Currently just ``projects_root`` (NULL = unconfigured → first-run setup).
Singleton invariant enforced by CHECK(id = 1); migration 0010 seeds the row.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class AppSettings(BaseModel):
    """Mirror of the app_settings singleton row."""

    projects_root: str | None
    updated_at: str


ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


class AppSettingsRepository:
    """Get/set the singleton app_settings row (id = 1)."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    def get(self) -> AppSettings:
        """Return the singleton row.

        Raises:
            RuntimeError: if the row is missing (migration 0010 not applied).
        """
        with self._factory() as conn:
            row = conn.execute(
                "SELECT projects_root, updated_at FROM app_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError(
                "app_settings row missing; check migration 0010 was applied"
            )
        return AppSettings(projects_root=row[0], updated_at=row[1])

    def set_projects_root(self, value: str | None) -> AppSettings:
        """Set (or clear with None) the configured projects root."""
        with self._factory() as conn:
            conn.execute(
                "UPDATE app_settings SET projects_root = ?, updated_at = ? WHERE id = 1",
                (value, datetime.now(UTC).isoformat()),
            )
        return self.get()

    @staticmethod
    def _row_to_model(row: Any) -> AppSettings:  # pragma: no cover - parity helper
        return AppSettings(projects_root=row[0], updated_at=row[1])
