"""RED tests for WU-2 — app lifespan VAPID keygen.

Tests run BEFORE the implementation exists; they must fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-3.7, SC-3.5
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_remote.app import create_app


@pytest.mark.anyio
async def test_lifespan_calls_get_or_create(
    tmp_db_path: Path,
    tmp_projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup must call VapidKeysRepository.get_or_create() after apply_migrations. (SC-3.5)"""
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_projects_root))

    with patch(
        "claude_remote.db.vapid_keys.VapidKeysRepository.get_or_create"
    ) as mock_get_or_create:
        mock_get_or_create.return_value = None  # simplest successful return
        app = create_app()
        async with app.router.lifespan_context(app):
            pass

    mock_get_or_create.assert_called_once()


@pytest.mark.anyio
async def test_lifespan_swallows_keygen_error(
    tmp_db_path: Path,
    tmp_projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If get_or_create() raises, lifespan must NOT crash — startup continues. (REQ-3.7)"""
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_projects_root))

    with patch(
        "claude_remote.db.vapid_keys.VapidKeysRepository.get_or_create",
        side_effect=RuntimeError("keygen exploded"),
    ):
        app = create_app()
        # Must not raise even though get_or_create raises
        async with app.router.lifespan_context(app):
            pass  # startup completed without crash


@pytest.mark.anyio
async def test_lifespan_inserts_vapid_row(
    tmp_db_path: Path,
    tmp_projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After successful startup, vapid_keys must have exactly 1 row. (SC-3.5)"""
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_projects_root))

    app = create_app()
    async with app.router.lifespan_context(app):
        conn = sqlite3.connect(tmp_db_path)
        count = conn.execute("SELECT COUNT(*) FROM vapid_keys").fetchone()[0]
        conn.close()

    assert count == 1
