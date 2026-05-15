"""RED tests for WU-1 — PushSubscriptionsRepository + migration 0007.

Tests run BEFORE the implementation exists; they must all fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-1, REQ-2 (SC-1.1–1.3, SC-2.1–2.5)
"""

from __future__ import annotations

import sqlite3
import time
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
# Migration 0007 — push_subscriptions table
# ---------------------------------------------------------------------------


class TestMigration0007:
    def test_0007_creates_push_subscriptions_table(self, tmp_path: Path) -> None:
        """Migration 0007 must create the push_subscriptions table."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "push_subscriptions" in tables

    def test_0007_table_has_all_seven_columns(self, tmp_path: Path) -> None:
        """push_subscriptions must have exactly the 7 schema columns."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(push_subscriptions)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        expected = {"id", "endpoint", "p256dh", "auth", "user_agent", "created_at", "last_seen_at"}
        assert expected <= col_names, f"Missing columns: {expected - col_names}"

    def test_0007_endpoint_unique_constraint(self, tmp_path: Path) -> None:
        """endpoint column must have a UNIQUE constraint (DB-level)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)"
            " VALUES ('https://push.example/sub1', 'k1', 'a1', ?)",
            (now,),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)"
                " VALUES ('https://push.example/sub1', 'k2', 'a2', ?)",
                (now,),
            )
        conn.close()

    def test_0007_user_agent_nullable(self, tmp_path: Path) -> None:
        """user_agent must be nullable — INSERT without it must succeed."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)"
            " VALUES ('https://push.example/no-ua', 'k1', 'a1', '2026-01-01T00:00:00+00:00')",
        )
        conn.commit()
        row = conn.execute(
            "SELECT user_agent FROM push_subscriptions WHERE endpoint=?",
            ("https://push.example/no-ua",),
        ).fetchone()
        conn.close()
        assert row[0] is None

    def test_0007_idempotent_reapply(self, tmp_path: Path) -> None:
        """Applying migrations twice must not raise or duplicate rows."""
        db = _migrated_db(tmp_path)
        apply_migrations(db, MIGRATIONS_DIR)  # second run
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename = '0007_create_push_subscriptions.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# PushSubscription model
# ---------------------------------------------------------------------------


class TestPushSubscriptionModel:
    def test_model_has_all_fields(self) -> None:
        """PushSubscription must have all 7 spec fields."""
        from claude_remote.db.push_subscriptions import PushSubscription

        sub = PushSubscription(
            id=1,
            endpoint="https://push.example/sub1",
            p256dh="k1",
            auth="a1",
            user_agent=None,
            created_at="2026-01-01T00:00:00+00:00",
            last_seen_at=None,
        )
        assert sub.id == 1
        assert sub.endpoint == "https://push.example/sub1"
        assert sub.p256dh == "k1"
        assert sub.auth == "a1"
        assert sub.user_agent is None
        assert sub.last_seen_at is None

    def test_model_accepts_user_agent_string(self) -> None:
        """user_agent must accept a string value."""
        from claude_remote.db.push_subscriptions import PushSubscription

        sub = PushSubscription(
            id=2,
            endpoint="https://push.example/sub2",
            p256dh="k2",
            auth="a2",
            user_agent="Mozilla/5.0",
            created_at="2026-01-01T00:00:00+00:00",
            last_seen_at="2026-01-01T01:00:00+00:00",
        )
        assert sub.user_agent == "Mozilla/5.0"


# ---------------------------------------------------------------------------
# PushSubscriptionsRepository
# ---------------------------------------------------------------------------


class TestPushSubscriptionsRepositoryCreate:
    def test_create_inserts_new_row(self, tmp_path: Path) -> None:
        """create() on empty table returns a PushSubscription with id >= 1. (SC-2.1)"""
        from claude_remote.db.push_subscriptions import (
            PushSubscription,
            PushSubscriptionsRepository,
        )

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        sub = repo.create(
            endpoint="https://push.example/sub1",
            p256dh="key1",
            auth="auth1",
            user_agent=None,
        )
        assert isinstance(sub, PushSubscription)
        assert sub.id >= 1
        assert sub.endpoint == "https://push.example/sub1"
        assert sub.p256dh == "key1"
        assert sub.auth == "auth1"
        assert sub.user_agent is None
        assert sub.last_seen_at is not None

    def test_create_sets_created_at(self, tmp_path: Path) -> None:
        """create() must set created_at to a non-empty ISO 8601 UTC string."""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        sub = repo.create(
            endpoint="https://push.example/sub2",
            p256dh="k2",
            auth="a2",
            user_agent="TestBrowser/1.0",
        )
        assert sub.created_at
        assert "T" in sub.created_at  # ISO format contains T
        assert sub.user_agent == "TestBrowser/1.0"

    def test_create_upsert_same_endpoint_updates_keys(self, tmp_path: Path) -> None:
        """Re-creating same endpoint updates p256dh/auth/last_seen_at, keeps created_at. (SC-2.2)"""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))

        first = repo.create(
            endpoint="https://push.example/E",
            p256dh="k1",
            auth="a1",
            user_agent=None,
        )
        # Brief sleep so timestamps differ
        time.sleep(0.01)
        second = repo.create(
            endpoint="https://push.example/E",
            p256dh="k2",
            auth="a2",
            user_agent="Mozilla",
        )

        # Keys must be updated
        assert second.p256dh == "k2"
        assert second.auth == "a2"
        assert second.user_agent == "Mozilla"
        # created_at must NOT change
        assert second.created_at == first.created_at
        # Only one row must exist
        conn = sqlite3.connect(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint=?",
            ("https://push.example/E",),
        ).fetchone()[0]
        conn.close()
        assert count == 1


class TestPushSubscriptionsRepositoryListAll:
    def test_list_all_returns_empty_list(self, tmp_path: Path) -> None:
        """list_all() on empty table returns []."""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        result = repo.list_all()
        assert result == []

    def test_list_all_returns_all_rows(self, tmp_path: Path) -> None:
        """list_all() returns all inserted rows."""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        repo.create(endpoint="https://push.example/s1", p256dh="k1", auth="a1", user_agent=None)
        repo.create(endpoint="https://push.example/s2", p256dh="k2", auth="a2", user_agent=None)
        result = repo.list_all()
        assert len(result) == 2

    def test_list_all_ordered_by_created_at_asc(self, tmp_path: Path) -> None:
        """list_all() must return rows ordered oldest-first (ASC). (SC-2.3)"""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        repo.create(endpoint="https://push.example/s1", p256dh="k1", auth="a1", user_agent=None)
        time.sleep(0.01)
        repo.create(endpoint="https://push.example/s2", p256dh="k2", auth="a2", user_agent=None)
        result = repo.list_all()
        assert len(result) == 2
        # First created must come first (ASC order)
        assert result[0].endpoint == "https://push.example/s1"
        assert result[1].endpoint == "https://push.example/s2"
        assert result[0].created_at <= result[1].created_at


class TestPushSubscriptionsRepositoryDelete:
    def test_delete_by_endpoint_hit_returns_true(self, tmp_path: Path) -> None:
        """delete_by_endpoint() returns True when row exists. (SC-2.4)"""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        repo.create(endpoint="https://push.example/E", p256dh="k1", auth="a1", user_agent=None)
        result = repo.delete_by_endpoint("https://push.example/E")
        assert result is True

    def test_delete_by_endpoint_removes_row(self, tmp_path: Path) -> None:
        """delete_by_endpoint() removes the row from the DB."""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        repo.create(endpoint="https://push.example/E", p256dh="k1", auth="a1", user_agent=None)
        repo.delete_by_endpoint("https://push.example/E")
        assert repo.list_all() == []

    def test_delete_by_endpoint_miss_returns_false(self, tmp_path: Path) -> None:
        """delete_by_endpoint() returns False when no matching row. (SC-2.5)"""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        result = repo.delete_by_endpoint("https://push.example/X")
        assert result is False

    def test_delete_by_endpoint_miss_does_not_raise(self, tmp_path: Path) -> None:
        """delete_by_endpoint() must not raise when endpoint not found."""
        from claude_remote.db.push_subscriptions import PushSubscriptionsRepository

        db = _migrated_db(tmp_path)
        repo = PushSubscriptionsRepository(_make_factory(db))
        # Should not raise
        repo.delete_by_endpoint("https://push.example/nonexistent")
