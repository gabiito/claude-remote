"""app_settings singleton + get_settings DB override (cfgroot WU-1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "t.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


def test_migration_creates_app_settings_singleton(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "app_settings" in tables
    row = conn.execute(
        "SELECT id, projects_root FROM app_settings WHERE id = 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1
    assert row[1] is None  # unconfigured on first run


def test_app_settings_repo_get_and_set(tmp_path: Path) -> None:
    from claude_remote.db.app_settings import AppSettingsRepository

    db = _db(tmp_path)
    repo = AppSettingsRepository(lambda: get_connection_for(db))

    assert repo.get().projects_root is None
    updated = repo.set_projects_root("/srv/work")
    assert updated.projects_root == "/srv/work"
    assert repo.get().projects_root == "/srv/work"
    # Clearing back to unconfigured.
    assert repo.set_projects_root(None).projects_root is None


def test_get_settings_unconfigured_then_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_remote.config import get_settings
    from claude_remote.db.app_settings import AppSettingsRepository

    db = _db(tmp_path)
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(db))

    s1 = get_settings()
    assert s1.configured is False  # app_settings.projects_root is NULL

    root = tmp_path / "chosen"
    root.mkdir()
    AppSettingsRepository(lambda: get_connection_for(db)).set_projects_root(str(root))

    s2 = get_settings()
    assert s2.configured is True
    assert s2.projects_root == root.resolve()


def test_settings_constructed_directly_is_configured() -> None:
    """Test/fixture construction stays valid and counts as configured."""
    from claude_remote.config import Settings

    s = Settings(db_path=Path("x.db"), projects_root=Path("/p"))
    assert s.configured is True
