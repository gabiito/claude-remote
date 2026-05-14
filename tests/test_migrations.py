"""Tests for the migrations runner.

WU-2 — RED tests (must fail until db/migrations.py is implemented).
"""

import sqlite3
from contextlib import suppress
from pathlib import Path

import pytest

from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _migration_rows(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT filename FROM schema_migrations ORDER BY rowid ASC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


class TestApplyOnce:
    def test_apply_once_returns_applied_filename(self, tmp_path: Path) -> None:
        """First run with 0001 migration returns that filename."""
        db = tmp_path / "test.db"
        result = apply_migrations(db, MIGRATIONS_DIR)
        assert "0001_create_projects.sql" in result

    def test_apply_once_creates_schema_migrations_table(self, tmp_path: Path) -> None:
        """schema_migrations table is created on fresh DB."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        assert "schema_migrations" in _table_names(db)

    def test_apply_once_creates_projects_table(self, tmp_path: Path) -> None:
        """projects table is created by 0001 migration."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        assert "projects" in _table_names(db)

    def test_schema_migrations_has_one_row(self, tmp_path: Path) -> None:
        """After one real migration, schema_migrations has exactly one row."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        rows = _migration_rows(db)
        assert len(rows) == 1
        assert rows[0] == "0001_create_projects.sql"


class TestIdempotency:
    def test_second_run_returns_empty_list(self, tmp_path: Path) -> None:
        """Calling apply_migrations twice returns [] on the second call."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        result = apply_migrations(db, MIGRATIONS_DIR)
        assert result == []

    def test_second_run_does_not_duplicate_schema_migrations(self, tmp_path: Path) -> None:
        """schema_migrations still has exactly one row after two runs."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        apply_migrations(db, MIGRATIONS_DIR)
        rows = _migration_rows(db)
        assert len(rows) == 1


class TestLexOrder:
    def test_lex_order_applied_ascending(self, tmp_path: Path) -> None:
        """Migrations are applied in lexicographic filename order."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        # Create three migrations — deliberately out of creation order
        (mig_dir / "0003_c.sql").write_text(
            "CREATE TABLE c_table (id TEXT PRIMARY KEY);"
        )
        (mig_dir / "0001_a.sql").write_text(
            "CREATE TABLE a_table (id TEXT PRIMARY KEY);"
        )
        (mig_dir / "0002_b.sql").write_text(
            "CREATE TABLE b_table (id TEXT PRIMARY KEY);"
        )
        db = tmp_path / "order.db"
        applied = apply_migrations(db, mig_dir)
        assert applied == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]
        rows = _migration_rows(db)
        assert rows == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]


class TestRollbackOnFailure:
    def test_malformed_sql_raises_exception(self, tmp_path: Path) -> None:
        """Invalid SQL causes an exception to be raised."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "0001_bad.sql").write_text("THIS IS NOT VALID SQL !!!;")
        db = tmp_path / "fail.db"
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(db, mig_dir)

    def test_malformed_sql_leaves_no_schema_migrations_row(self, tmp_path: Path) -> None:
        """After a failed migration, no row is inserted into schema_migrations."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "0001_bad.sql").write_text("THIS IS NOT VALID SQL !!!;")
        db = tmp_path / "fail.db"
        with suppress(sqlite3.OperationalError):
            apply_migrations(db, mig_dir)
        # schema_migrations table may exist but must have no rows for the failed file
        conn = sqlite3.connect(db)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "schema_migrations" in tables:
            rows = conn.execute(
                "SELECT filename FROM schema_migrations"
            ).fetchall()
            assert rows == [], "Failed migration must not be recorded in schema_migrations"
        conn.close()

    def test_good_migration_followed_by_bad_stops_at_bad(self, tmp_path: Path) -> None:
        """A good migration followed by a bad one: only the good one is recorded."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "0001_good.sql").write_text(
            "CREATE TABLE good_table (id TEXT PRIMARY KEY);"
        )
        (mig_dir / "0002_bad.sql").write_text("INVALID SQL;")
        db = tmp_path / "partial.db"
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(db, mig_dir)
        rows = _migration_rows(db)
        assert rows == ["0001_good.sql"], "Only the successful migration should be recorded"
