import os
import sqlite3

import pytest

from claude_remote.db.connection import get_connection


def test_get_connection_returns_sqlite_connection(tmp_path: pytest.TempPathFactory) -> None:
    db_path = str(tmp_path / "test.db")  # type: ignore[operator]
    os.environ["CLAUDE_REMOTE_DB_PATH"] = db_path
    try:
        with get_connection() as conn:
            assert isinstance(conn, sqlite3.Connection)
    finally:
        del os.environ["CLAUDE_REMOTE_DB_PATH"]


def test_get_connection_uses_row_factory(tmp_path: pytest.TempPathFactory) -> None:
    db_path = str(tmp_path / "test.db")  # type: ignore[operator]
    os.environ["CLAUDE_REMOTE_DB_PATH"] = db_path
    try:
        with get_connection() as conn:
            assert conn.row_factory is sqlite3.Row
    finally:
        del os.environ["CLAUDE_REMOTE_DB_PATH"]


def test_get_connection_commits_on_success(tmp_path: pytest.TempPathFactory) -> None:
    db_path = str(tmp_path / "test.db")  # type: ignore[operator]
    os.environ["CLAUDE_REMOTE_DB_PATH"] = db_path
    try:
        with get_connection() as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        # Second context manager confirms table persisted (commit happened)
        with get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "t" in tables
    finally:
        del os.environ["CLAUDE_REMOTE_DB_PATH"]
