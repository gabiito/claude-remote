"""Password hashing + secret generation (auth WU-1).

scrypt via hashlib (stdlib — no new deps). Hash is salted and self-
describing (params embedded) so it can evolve without a data migration.
"""

from __future__ import annotations

import pytest


def test_hash_verify_roundtrip() -> None:
    from claude_remote.services.auth import hash_password, verify_password

    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_wrong_password_rejected() -> None:
    from claude_remote.services.auth import hash_password, verify_password

    h = hash_password("s3cret")
    assert verify_password("s3cret ", h) is False
    assert verify_password("", h) is False
    assert verify_password("S3cret", h) is False


def test_salt_makes_hashes_unique() -> None:
    from claude_remote.services.auth import hash_password

    assert hash_password("same") != hash_password("same")


def test_hash_is_self_describing_scrypt() -> None:
    from claude_remote.services.auth import hash_password

    h = hash_password("x")
    assert h.startswith("scrypt$")
    # scrypt$N$r$p$salt$hash
    assert len(h.split("$")) == 6


def test_verify_tolerates_garbage_stored_value() -> None:
    from claude_remote.services.auth import verify_password

    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "scrypt$bad") is False


def test_session_secret_is_random_and_urlsafe() -> None:
    from claude_remote.services.auth import generate_session_secret

    a = generate_session_secret()
    b = generate_session_secret()
    assert a != b
    assert len(a) >= 32
    assert a.strip() == a


# --- AppSettingsRepository auth columns (migration 0011) ---


@pytest.fixture()
def repo(tmp_path):
    from claude_remote.db.app_settings import AppSettingsRepository
    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

    db = tmp_path / "auth.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return AppSettingsRepository(lambda: get_connection_for(db))


def test_password_hash_defaults_none_then_set(repo) -> None:
    assert repo.get().password_hash is None
    repo.set_password_hash("scrypt$1$1$1$aa$bb")
    assert repo.get().password_hash == "scrypt$1$1$1$aa$bb"


def test_session_secret_get_or_create_is_stable(repo) -> None:
    assert repo.get().session_secret is None
    s1 = repo.get_or_create_session_secret()
    s2 = repo.get_or_create_session_secret()
    assert s1 == s2
    assert repo.get().session_secret == s1


# --- claudio set-password CLI command ---


def test_cli_set_password_writes_hash(tmp_path, monkeypatch) -> None:
    from claude_remote import cli
    from claude_remote.db.app_settings import AppSettingsRepository
    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

    db = tmp_path / "cli.db"
    apply_migrations(db, MIGRATIONS_DIR)
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(db))

    prompts = iter(["hunter2", "hunter2"])
    rc = cli.set_password(prompt=lambda _label: next(prompts))
    assert rc == 0

    repo = AppSettingsRepository(lambda: get_connection_for(db))
    from claude_remote.services.auth import verify_password

    stored = repo.get().password_hash
    assert stored and verify_password("hunter2", stored)
    assert repo.get().session_secret  # secret provisioned on first set


def test_cli_set_password_mismatch_aborts(tmp_path, monkeypatch) -> None:
    from claude_remote import cli
    from claude_remote.db.app_settings import AppSettingsRepository
    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

    db = tmp_path / "cli2.db"
    apply_migrations(db, MIGRATIONS_DIR)
    monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", str(db))

    prompts = iter(["a", "b"])
    rc = cli.set_password(prompt=lambda _label: next(prompts))
    assert rc != 0
    repo = AppSettingsRepository(lambda: get_connection_for(db))
    assert repo.get().password_hash is None


def test_cli_parser_accepts_set_password() -> None:
    from claude_remote import cli

    ns = cli._parser().parse_args(["set-password"])
    assert ns.command == "set-password"
