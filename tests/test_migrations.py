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

    def test_schema_migrations_has_expected_rows(self, tmp_path: Path) -> None:
        """After applying MIGRATIONS_DIR, schema_migrations has one row per SQL file."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        rows = _migration_rows(db)
        # Number of rows must equal number of .sql files in MIGRATIONS_DIR
        sql_file_count = len(list(MIGRATIONS_DIR.glob("*.sql")))
        assert len(rows) == sql_file_count
        assert rows[0] == "0001_create_projects.sql"


class TestIdempotency:
    def test_second_run_returns_empty_list(self, tmp_path: Path) -> None:
        """Calling apply_migrations twice returns [] on the second call."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        result = apply_migrations(db, MIGRATIONS_DIR)
        assert result == []

    def test_second_run_does_not_duplicate_schema_migrations(self, tmp_path: Path) -> None:
        """schema_migrations row count is stable after two runs (no duplicates)."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        first_count = len(_migration_rows(db))
        apply_migrations(db, MIGRATIONS_DIR)
        rows = _migration_rows(db)
        assert len(rows) == first_count  # idempotent — same count


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


# ---------------------------------------------------------------------------
# WU-1 migration tests — 0003_create_events.sql
# ---------------------------------------------------------------------------


class TestEvents0003Migration:
    def test_0003_creates_events_table(self, tmp_path: Path) -> None:
        """Migration 0003 must create the events table."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        assert "events" in _table_names(db)

    def test_0003_events_table_has_all_columns(self, tmp_path: Path) -> None:
        """events table must have the 6 expected columns."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(events)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        expected = {"id", "instance_id", "project_id", "event_type", "payload", "received_at"}
        assert expected <= col_names, f"Missing columns: {expected - col_names}"

    def test_0003_check_constraint_enforces_event_type(self, tmp_path: Path) -> None:
        """CHECK constraint must reject invalid event_type values."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (id, instance_id, project_id, event_type, payload, received_at)"
                " VALUES ('x', NULL, NULL, 'BadType', '{}', '2026-01-01T00:00:00+00:00')"
            )
        conn.close()

    def test_0003_idempotent(self, tmp_path: Path) -> None:
        """Applying migrations twice leaves exactly one schema_migrations row for 0003."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations WHERE filename = '0003_create_events.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_0003_creates_indexes(self, tmp_path: Path) -> None:
        """Migration 0003 must create both composite indexes on events."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
        ).fetchall()
        conn.close()
        index_names = {r[0] for r in rows}
        assert "idx_events_project_received" in index_names
        assert "idx_events_instance_received" in index_names


# ---------------------------------------------------------------------------
# WU-2 migration tests — 0004_add_hook_token_to_instances.sql
# ---------------------------------------------------------------------------


class TestHookToken0004Migration:
    def test_0004_adds_hook_token_column(self, tmp_path: Path) -> None:
        """Migration 0004 must add hook_token column to instances table."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(instances)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        assert "hook_token" in col_names

    def test_0004_creates_unique_index_on_hook_token(self, tmp_path: Path) -> None:
        """Migration 0004 must create a UNIQUE index on instances.hook_token."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='instances'"
        ).fetchall()
        conn.close()
        index_names = {r[0] for r in rows}
        assert "idx_instances_hook_token" in index_names

    def test_0004_backfills_existing_rows_with_distinct_tokens(self, tmp_path: Path) -> None:
        """Pre-migration rows must be backfilled with distinct non-null tokens."""
        import shutil
        import sqlite3 as _sqlite3
        import uuid
        from datetime import UTC, datetime

        from claude_remote.db.migrations import MIGRATIONS_DIR as REAL_MIGS_DIR

        db = tmp_path / "test.db"
        # Apply migrations 0001 + 0002 (NOT 0003/0004) via a temp migration dir
        mig_dir = tmp_path / "migs_pre"
        mig_dir.mkdir()

        for f in sorted(REAL_MIGS_DIR.glob("*.sql")):
            if f.name in ("0003_create_events.sql", "0004_add_hook_token_to_instances.sql"):
                continue
            shutil.copy(f, mig_dir / f.name)

        apply_migrations(db, mig_dir)

        # Insert two pre-migration instance rows (no hook_token column yet)
        conn = _sqlite3.connect(db)
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())

        # We need a project first (projects table exists from 0001 migration)
        proj_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO projects (id, slug, name, path, domain, created_at)"
            " VALUES (?, 'p1', 'P1', '/tmp/p1', 'sandbox', ?)",
            (proj_id, now),
        )
        conn.execute(
            "INSERT INTO instances"
            " (id, project_id, tmux_session_name, pane_pid, status, created_at, stopped_at)"
            " VALUES (?, ?, 'claude-remote-p1-aa000001', NULL, 'running', ?, NULL)",
            (id1, proj_id, now),
        )
        conn.execute(
            "INSERT INTO instances"
            " (id, project_id, tmux_session_name, pane_pid, status, created_at, stopped_at)"
            " VALUES (?, ?, 'claude-remote-p1-bb000001', NULL, 'running', ?, NULL)",
            (id2, proj_id, now),
        )
        conn.commit()
        conn.close()

        # Now apply the full migration set (0003 + 0004)
        apply_migrations(db, MIGRATIONS_DIR)

        # Verify backfill
        conn = _sqlite3.connect(db)
        rows = conn.execute(
            "SELECT hook_token FROM instances WHERE id IN (?, ?)", (id1, id2)
        ).fetchall()
        conn.close()

        tokens = [r[0] for r in rows]
        assert all(t is not None and len(t) > 0 for t in tokens), f"Tokens not backfilled: {tokens}"
        assert len(set(tokens)) == 2, f"Tokens must be distinct: {tokens}"

    def test_0004_idempotent(self, tmp_path: Path) -> None:
        """Applying migrations twice leaves exactly one schema_migrations row for 0004."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename = '0004_add_hook_token_to_instances.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# WU-3 (mvp-project-discovery) — 0005_add_is_stale_to_projects.sql
# ---------------------------------------------------------------------------


class TestIsStale0005Migration:
    def test_0005_adds_is_stale_column(self, tmp_path: Path) -> None:
        """Migration 0005 must add is_stale column to projects table."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(projects)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        assert "is_stale" in col_names

    def test_0005_is_stale_defaults_to_zero(self, tmp_path: Path) -> None:
        """Migration 0005 must set is_stale=0 (DEFAULT 0) for new rows."""
        import uuid
        from datetime import UTC, datetime

        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        now = datetime.now(UTC).isoformat()
        proj_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO projects (id, slug, name, path, domain, created_at)"
            " VALUES (?, 'p1', 'P1', '/tmp/p1', 'test', ?)",
            (proj_id, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_stale FROM projects WHERE id = ?", (proj_id,)
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_0005_idempotent(self, tmp_path: Path) -> None:
        """Applying migrations twice leaves exactly one schema_migrations row for 0005."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename = '0005_add_is_stale_to_projects.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# WU-5 migration tests — 0009_drop_ntfy_topic.sql
# ---------------------------------------------------------------------------


class TestDropNtfyTopic0009Migration:
    def test_0009_drops_ntfy_topic_on_modern_sqlite(self, tmp_path: Path) -> None:
        """Migration 0009 drops ntfy_topic when SQLite >= 3.35.0."""
        import sqlite3 as _sqlite3

        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        conn = _sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(notification_preferences)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        # On modern SQLite (Python 3.12 ships >= 3.40), ntfy_topic should be gone
        if _sqlite3.sqlite_version_info >= (3, 35, 0):
            assert "ntfy_topic" not in col_names
        else:
            # On old SQLite, column stays but no exception raised
            assert "ntfy_topic" in col_names

    def test_0009_skips_gracefully_on_simulated_old_sqlite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Migration 0009 is skipped and recorded as _skipped: on SQLite < 3.35.0."""
        import sqlite3 as _sqlite3

        # Monkey-patch sqlite_version_info to simulate old SQLite
        monkeypatch.setattr(_sqlite3, "sqlite_version_info", (3, 34, 0))

        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)

        conn = _sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename LIKE '%0009_drop_ntfy_topic%'"
        ).fetchall()
        col_rows = conn.execute("PRAGMA table_info(notification_preferences)").fetchall()
        conn.close()

        # Exactly one record for 0009 (either applied or _skipped:)
        assert len(rows) == 1
        # The _skipped: record must exist (not the normal filename)
        assert rows[0][0].startswith("_skipped:")

        # ntfy_topic column must still be there (skip = no ALTER)
        col_names = {r[1] for r in col_rows}
        assert "ntfy_topic" in col_names

    def test_0009_idempotent(self, tmp_path: Path) -> None:
        """Running apply_migrations twice records 0009 exactly once."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename LIKE '%0009_drop_ntfy_topic%'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_schema_migrations_count_includes_0009(self, tmp_path: Path) -> None:
        """After all migrations, schema_migrations has one row per SQL file."""
        db = tmp_path / "test.db"
        apply_migrations(db, MIGRATIONS_DIR)
        rows = _migration_rows(db)
        sql_file_count = len(list(MIGRATIONS_DIR.glob("*.sql")))
        assert len(rows) == sql_file_count
