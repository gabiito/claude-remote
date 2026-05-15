"""RED tests for WU-2 — VapidKeysRepository + migration 0008.

Tests run BEFORE the implementation exists; they must all fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-3 (SC-3.1–3.5)
"""

from __future__ import annotations

import base64
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
# Migration 0008 — vapid_keys table
# ---------------------------------------------------------------------------


class TestMigration0008:
    def test_0008_creates_vapid_keys_table(self, tmp_path: Path) -> None:
        """Migration 0008 must create the vapid_keys table."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "vapid_keys" in tables

    def test_0008_table_has_all_four_columns(self, tmp_path: Path) -> None:
        """vapid_keys must have: id, public_key, private_key, created_at."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        rows = conn.execute("PRAGMA table_info(vapid_keys)").fetchall()
        conn.close()
        col_names = {r[1] for r in rows}
        expected = {"id", "public_key", "private_key", "created_at"}
        assert expected <= col_names, f"Missing columns: {expected - col_names}"

    def test_0008_empty_after_migration(self, tmp_path: Path) -> None:
        """vapid_keys table must be empty after migration (lifespan seeds it)."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM vapid_keys").fetchone()[0]
        conn.close()
        assert count == 0

    def test_0008_check_id1_constraint(self, tmp_path: Path) -> None:
        """CHECK(id=1) must prevent inserting id=2."""
        db = _migrated_db(tmp_path)
        conn = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO vapid_keys (id, public_key, private_key, created_at)"
                " VALUES (2, 'pub', 'priv', '2026-01-01T00:00:00+00:00')"
            )
        conn.close()

    def test_0008_idempotent_reapply(self, tmp_path: Path) -> None:
        """Applying migrations twice must not raise or duplicate rows."""
        db = _migrated_db(tmp_path)
        apply_migrations(db, MIGRATIONS_DIR)  # second run
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT filename FROM schema_migrations"
            " WHERE filename = '0008_create_vapid_keys.sql'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# VapidKeys model
# ---------------------------------------------------------------------------


class TestVapidKeysModel:
    def test_model_has_all_fields(self) -> None:
        """VapidKeys must have id, public_key, private_key, created_at fields (REQ-3.3)."""
        from claude_remote.db.vapid_keys import VapidKeys

        keys = VapidKeys(
            id=1,
            public_key="BPub123",
            private_key="-----BEGIN PRIVATE KEY-----\n...",
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert keys.id == 1
        assert keys.public_key == "BPub123"
        assert "BEGIN PRIVATE KEY" in keys.private_key
        assert keys.created_at == "2026-01-01T00:00:00+00:00"

    def test_get_returns_model_with_id_one(self, tmp_path: Path) -> None:
        """VapidKeysRepository.get() returns a model whose id == 1 (REQ-3.3)."""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        repo.get_or_create()
        keys = repo.get()
        assert keys.id == 1


# ---------------------------------------------------------------------------
# VapidKeysRepository
# ---------------------------------------------------------------------------


class TestVapidKeysRepositoryGetOrCreate:
    def test_get_or_create_generates_keypair_on_fresh_db(self, tmp_path: Path) -> None:
        """get_or_create() on empty vapid_keys returns VapidKeys with non-empty fields. (SC-3.1)"""
        from claude_remote.db.vapid_keys import VapidKeys, VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        keys = repo.get_or_create()
        assert isinstance(keys, VapidKeys)
        assert len(keys.public_key) > 0
        assert len(keys.private_key) > 0
        assert len(keys.created_at) > 0

    def test_get_or_create_inserts_exactly_one_row(self, tmp_path: Path) -> None:
        """get_or_create() inserts exactly 1 row in vapid_keys. (SC-3.1)"""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        repo.get_or_create()
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM vapid_keys").fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_or_create_idempotent(self, tmp_path: Path) -> None:
        """Second get_or_create() returns the same keypair (no rotation). (SC-3.2)"""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        first = repo.get_or_create()
        second = repo.get_or_create()
        assert first.public_key == second.public_key
        assert first.private_key == second.private_key
        # Still exactly 1 row
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM vapid_keys").fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_or_create_race_simulation(self, tmp_path: Path) -> None:
        """If row already inserted by another writer, get_or_create returns that row."""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        # Pre-insert a row (simulate first writer)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO vapid_keys (id, public_key, private_key, created_at)"
            " VALUES (1, 'pub-winner', 'priv-winner', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        repo = VapidKeysRepository(_make_factory(db))
        keys = repo.get_or_create()
        # Must return the first writer's row
        assert keys.public_key == "pub-winner"
        assert keys.private_key == "priv-winner"


class TestVapidKeysRepositoryGet:
    def test_get_raises_when_no_row(self, tmp_path: Path) -> None:
        """get() raises RuntimeError when vapid_keys table is empty. (SC-3.4)"""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        with pytest.raises(RuntimeError):
            repo.get()

    def test_get_returns_existing_row(self, tmp_path: Path) -> None:
        """get() returns the singleton row when it exists."""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        repo.get_or_create()  # creates the row
        keys = repo.get()
        assert keys.public_key
        assert keys.private_key


class TestPublicKeyUncompressed:
    def test_public_key_uncompressed_format(self, tmp_path: Path) -> None:
        """public_key_uncompressed() returns URL-safe base64url, decodes to 65 bytes. (SC-3.3)"""
        from claude_remote.db.vapid_keys import VapidKeysRepository

        db = _migrated_db(tmp_path)
        repo = VapidKeysRepository(_make_factory(db))
        repo.get_or_create()
        result = repo.public_key_uncompressed()
        # Must be non-empty
        assert result
        # Must contain only base64url chars (no + / = in URL-safe)
        _b64url_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in _b64url_chars for c in result)
        # Must decode to exactly 65 bytes (0x04 prefix + 32 X + 32 Y)
        # Add padding back for decode
        padding = "=" * ((4 - len(result) % 4) % 4)
        decoded = base64.urlsafe_b64decode(result + padding)
        assert len(decoded) == 65
        assert decoded[0] == 0x04  # uncompressed point prefix
