"""Red tests for lifespan applying migrations on startup — WU-7.

Strategy: enter the app's lifespan context manager directly
(app.router.lifespan_context) and verify side effects.

Settings are injected via monkeypatch.setenv so get_settings()
reads the tmp paths during lifespan startup.
"""

import sqlite3
from pathlib import Path

import pytest

from claude_remote.app import create_app


@pytest.mark.anyio()
async def test_lifespan_applies_migrations(
    tmp_db_path: Path,
    tmp_projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the lifespan creates schema_migrations and projects tables."""
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_projects_root))

    app = create_app()
    async with app.router.lifespan_context(app):
        conn = sqlite3.connect(tmp_db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()

    table_names = {r[0] for r in rows}
    assert "projects" in table_names
    assert "schema_migrations" in table_names


@pytest.mark.anyio()
async def test_lifespan_idempotent(
    tmp_db_path: Path,
    tmp_projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering lifespan twice does not raise (migrations are idempotent)."""
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_projects_root))

    app = create_app()
    async with app.router.lifespan_context(app):
        pass  # first entry
    async with app.router.lifespan_context(app):
        pass  # second entry — must not raise
