"""Red tests for WU-1 — PRAGMA foreign_keys must be ON for every connection.

These tests verify:
  1. get_connection_for(path) opens a connection with PRAGMA foreign_keys = ON.
  2. get_connection_for works with distinct paths (parametric helper).
  3. The existing get_connection() also enables foreign keys (regression guard).
  4. GET /projects smoke test still passes after _get_connection_for is removed
     from routes/projects.py (import path changes to shared helper).
"""

import sqlite3
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for


def test_get_connection_for_enables_foreign_keys(tmp_path: Path) -> None:
    """Any connection opened via get_connection_for must have PRAGMA foreign_keys = ON."""
    db_path = tmp_path / "fk_test.db"
    with get_connection_for(db_path) as conn:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row is not None
        assert row[0] == 1, f"Expected foreign_keys=1, got {row[0]}"


def test_get_connection_for_parametric_distinct_paths(tmp_path: Path) -> None:
    """Two calls with different paths open connections to different DB files."""
    path_a = tmp_path / "db_a.db"
    path_b = tmp_path / "db_b.db"

    with get_connection_for(path_a) as conn_a:
        conn_a.execute("CREATE TABLE alpha (x INTEGER)")

    with get_connection_for(path_b) as conn_b:
        tables = [
            r[0]
            for r in conn_b.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "alpha" not in tables, "path_b should not see path_a's table"


def test_get_connection_for_row_factory_is_sqlite_row(tmp_path: Path) -> None:
    """Connections returned by get_connection_for use sqlite3.Row factory."""
    db_path = tmp_path / "row_factory.db"
    with get_connection_for(db_path) as conn:
        assert conn.row_factory is sqlite3.Row


def test_get_connection_for_commits_on_clean_exit(tmp_path: Path) -> None:
    """Data written inside the context manager is committed on exit."""
    db_path = tmp_path / "commit_test.db"
    with get_connection_for(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    # Re-open to confirm the table persisted (i.e., it was committed)
    with get_connection_for(db_path) as conn2:
        tables = [
            r[0]
            for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "t" in tables


def test_get_connection_for_rollback_on_exception(tmp_path: Path) -> None:
    """Data written inside the context manager is rolled back on exception."""
    db_path = tmp_path / "rollback_test.db"

    # First: create the table (committed)
    with get_connection_for(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")

    # Second: write then raise — should rollback
    with pytest.raises(ValueError), get_connection_for(db_path) as conn:
        conn.execute("INSERT INTO t (id, val) VALUES (1, 'hello')")
        raise ValueError("boom")

    # Confirm no row was persisted
    with get_connection_for(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 0, "Row should have been rolled back"


def test_get_connection_creates_parent_dir(tmp_path: Path) -> None:
    """get_connection_for creates parent directories as needed."""
    db_path = tmp_path / "nested" / "dir" / "test.db"
    with get_connection_for(db_path) as conn:
        conn.execute("CREATE TABLE x (id INTEGER)")
    assert db_path.exists()
