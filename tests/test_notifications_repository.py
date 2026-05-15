"""Red tests for WU-1 — NotificationsRepository + migration 0006 + env-var override.

Tests run BEFORE the implementation exists; they must all fail (ImportError / AttributeError).
Once the green commit lands, all tests here must pass.

Covers:
  - Migration 0006 creates table + seeds singleton row
  - CHECK(id=1) constraint
  - NotificationPreferences pydantic model
  - NotificationsRepository.get() / update()
  - Env-var override at startup (CLAUDE_REMOTE_NTFY_TOPIC)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_factory(db_path: Path):
    return lambda: get_connection_for(db_path)


def _migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


# ---------------------------------------------------------------------------
# Migration 0006 — table + seed row
# ---------------------------------------------------------------------------


class TestMigration0006:
    def test_0006_creates_notification_preferences_table(self, tmp_path: Path) -> None:
        """Migration 0006 must create the notification_preferences table."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "notification_preferences" in tables

    def test_0006_seeds_singleton_row(self, tmp_path: Path) -> None:
        """Migration 0006 must insert exactly one row with id=1."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT id FROM notification_preferences").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == 1

    def test_0006_default_notify_on_notification_is_1(self, tmp_path: Path) -> None:
        """notify_on_notification must default to 1 (ON)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT notify_on_notification FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_0006_other_toggles_default_to_0(self, tmp_path: Path) -> None:
        """All other toggles must default to 0 (OFF)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT notify_on_stop, notify_on_session_end, notify_on_session_start,"
            " notify_on_pre_tool_use, notify_on_post_tool_use"
            " FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        assert all(v == 0 for v in row), f"Expected all zeros, got {list(row)}"

    def test_0006_quiet_hours_null_by_default(self, tmp_path: Path) -> None:
        """quiet_hours_start and quiet_hours_end must be NULL in the seeded row."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT quiet_hours_start, quiet_hours_end FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        assert row[0] is None
        assert row[1] is None

    def test_0006_ntfy_topic_is_nonempty_hex(self, tmp_path: Path) -> None:
        """ntfy_topic must be a non-empty lowercase hex string (32 chars)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT ntfy_topic FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        topic = row[0]
        assert topic is not None
        assert len(topic) == 32
        assert topic == topic.lower()
        assert all(c in "0123456789abcdef" for c in topic)

    def test_0006_updated_at_is_nonempty_string(self, tmp_path: Path) -> None:
        """updated_at must be a non-empty string (ISO 8601-ish)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT updated_at FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert len(row[0]) > 0

    def test_0006_check_constraint_rejects_id_2(self, tmp_path: Path) -> None:
        """CHECK(id=1) must prevent inserting a row with id=2."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notification_preferences"
                " (id, ntfy_topic, updated_at)"
                " VALUES (2, 'abc', 'now')"
            )
        conn.close()

    def test_0006_idempotent_reapply(self, tmp_path: Path) -> None:
        """Re-applying all migrations must not duplicate the singleton row."""
        db = _migrated_db(tmp_path)
        apply_migrations(db, MIGRATIONS_DIR)  # second run
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT id FROM notification_preferences").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_0006_has_exactly_one_schema_migrations_row(self, tmp_path: Path) -> None:
        """schema_migrations must record migration 0006 exactly once."""
        db = _migrated_db(tmp_path)
        apply_migrations(db, MIGRATIONS_DIR)  # idempotent second run
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename = '0006_create_notification_preferences.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# NotificationPreferences model
# ---------------------------------------------------------------------------


class TestNotificationPreferencesModel:
    def test_model_boolifies_integer_columns(self) -> None:
        """INTEGER columns (0/1) must map to bool in the pydantic model."""
        from claude_remote.db.notifications import NotificationPreferences

        prefs = NotificationPreferences(
            notify_on_notification=1,  # type: ignore[arg-type]
            notify_on_stop=0,  # type: ignore[arg-type]
            notify_on_session_end=0,  # type: ignore[arg-type]
            notify_on_session_start=0,  # type: ignore[arg-type]
            notify_on_pre_tool_use=0,  # type: ignore[arg-type]
            notify_on_post_tool_use=0,  # type: ignore[arg-type]
            quiet_hours_start=None,
            quiet_hours_end=None,
            ntfy_topic="abc",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert prefs.notify_on_notification is True
        assert prefs.notify_on_stop is False

    def test_model_accepts_none_quiet_hours(self) -> None:
        """quiet_hours_start/end must accept None."""
        from claude_remote.db.notifications import NotificationPreferences

        prefs = NotificationPreferences(
            notify_on_notification=True,
            notify_on_stop=False,
            notify_on_session_end=False,
            notify_on_session_start=False,
            notify_on_pre_tool_use=False,
            notify_on_post_tool_use=False,
            quiet_hours_start=None,
            quiet_hours_end=None,
            ntfy_topic="abc",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert prefs.quiet_hours_start is None
        assert prefs.quiet_hours_end is None


# ---------------------------------------------------------------------------
# NotificationsRepository
# ---------------------------------------------------------------------------


class TestNotificationsRepositoryGet:
    def test_get_returns_singleton_with_id1(self, tmp_path: Path) -> None:
        """get() must return a NotificationPreferences with notify_on_notification=True."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        prefs = repo.get()
        assert prefs.notify_on_notification is True
        assert prefs.notify_on_stop is False

    def test_get_returns_correct_defaults(self, tmp_path: Path) -> None:
        """get() must return all 6 toggles with correct default values."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        prefs = repo.get()
        assert prefs.notify_on_notification is True
        assert prefs.notify_on_stop is False
        assert prefs.notify_on_session_end is False
        assert prefs.notify_on_session_start is False
        assert prefs.notify_on_pre_tool_use is False
        assert prefs.notify_on_post_tool_use is False

    def test_get_raises_runtime_error_when_row_missing(self, tmp_path: Path) -> None:
        """get() must raise RuntimeError when the singleton row is missing."""
        from claude_remote.db.notifications import NotificationsRepository

        db = tmp_path / "empty.db"
        # Apply only 0001-0005 migrations by creating a DB with just the table
        # but no row — we DELETE the row after applying migrations.
        apply_migrations(db, MIGRATIONS_DIR)
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM notification_preferences WHERE id=1")
        conn.commit()
        conn.close()

        repo = NotificationsRepository(_make_factory(db))
        with pytest.raises(RuntimeError, match="migration 0006"):
            repo.get()

    def test_get_ntfy_topic_is_nonempty(self, tmp_path: Path) -> None:
        """get() must return a non-empty ntfy_topic."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        prefs = repo.get()
        assert prefs.ntfy_topic
        assert len(prefs.ntfy_topic) > 0


class TestNotificationsRepositoryUpdate:
    def test_update_flips_one_toggle(self, tmp_path: Path) -> None:
        """update(notify_on_stop=True) must flip only that column."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        updated = repo.update(notify_on_stop=True)
        assert updated.notify_on_stop is True
        assert updated.notify_on_notification is True  # unchanged

    def test_update_preserves_other_fields(self, tmp_path: Path) -> None:
        """update() must not alter columns not in kwargs."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        original = repo.get()
        updated = repo.update(notify_on_session_start=True)
        assert updated.notify_on_notification == original.notify_on_notification
        assert updated.notify_on_stop == original.notify_on_stop
        assert updated.ntfy_topic == original.ntfy_topic

    def test_update_stamps_updated_at(self, tmp_path: Path) -> None:
        """update() must produce a later updated_at than the original."""
        import time

        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        original = repo.get()
        time.sleep(0.01)  # ensure clock advances
        updated = repo.update(notify_on_stop=True)
        assert updated.updated_at >= original.updated_at

    def test_update_with_unknown_key_raises_value_error(self, tmp_path: Path) -> None:
        """update() must raise ValueError on unknown field names."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        with pytest.raises(ValueError, match="Unknown preference fields"):
            repo.update(nonexistent_field=True)  # type: ignore[call-arg]

    def test_update_bool_normalizes_to_int_in_db(self, tmp_path: Path) -> None:
        """bool True/False must round-trip through DB as 1/0."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        repo.update(notify_on_stop=True)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT notify_on_stop FROM notification_preferences WHERE id=1"
        ).fetchone()
        conn.close()
        assert row[0] == 1  # stored as integer 1

    def test_update_quiet_hours_to_string(self, tmp_path: Path) -> None:
        """update() must persist quiet_hours_start as a string."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        updated = repo.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
        assert updated.quiet_hours_start == "22:00"
        assert updated.quiet_hours_end == "08:00"

    def test_update_quiet_hours_to_none(self, tmp_path: Path) -> None:
        """update() must persist NULL for quiet_hours when set to None."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        repo.update(quiet_hours_start="22:00")
        updated = repo.update(quiet_hours_start=None)
        assert updated.quiet_hours_start is None

    def test_update_empty_kwargs_returns_current_prefs(self, tmp_path: Path) -> None:
        """update() with no kwargs must return current prefs unchanged."""
        from claude_remote.db.notifications import NotificationsRepository

        db = _migrated_db(tmp_path)
        repo = NotificationsRepository(_make_factory(db))
        prefs = repo.update()
        assert prefs.notify_on_notification is True


# ---------------------------------------------------------------------------
# Env-var override — CLAUDE_REMOTE_NTFY_TOPIC
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    def test_env_var_override_writes_to_db_at_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_REMOTE_NTFY_TOPIC set at startup must write-through to DB row."""
        monkeypatch.setenv("CLAUDE_REMOTE_NTFY_TOPIC", "my-custom-topic")

        db = _migrated_db(tmp_path)

        # Simulate the startup override logic (as implemented in app.py lifespan)
        from claude_remote.config import get_settings
        from claude_remote.db.notifications import NotificationsRepository

        settings = get_settings()
        repo = NotificationsRepository(_make_factory(db))
        if settings.ntfy_topic_override:
            try:
                repo.update(ntfy_topic=settings.ntfy_topic_override)
            except Exception:
                pass

        prefs = repo.get()
        assert prefs.ntfy_topic == "my-custom-topic"

    def test_env_var_not_set_leaves_topic_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CLAUDE_REMOTE_NTFY_TOPIC is not set, DB topic remains the seeded value."""
        monkeypatch.delenv("CLAUDE_REMOTE_NTFY_TOPIC", raising=False)

        db = _migrated_db(tmp_path)
        from claude_remote.db.notifications import NotificationsRepository

        repo = NotificationsRepository(_make_factory(db))
        original_topic = repo.get().ntfy_topic

        # When env var not set, no override happens
        from claude_remote.config import get_settings

        settings = get_settings()
        assert settings.ntfy_topic_override is None

        # Topic stays as seeded
        assert repo.get().ntfy_topic == original_topic

    def test_config_exposes_ntfy_topic_override_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Settings must have ntfy_topic_override field."""
        monkeypatch.setenv("CLAUDE_REMOTE_NTFY_TOPIC", "overridden")
        from claude_remote.config import get_settings

        settings = get_settings()
        assert settings.ntfy_topic_override == "overridden"
