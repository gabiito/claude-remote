"""RED tests for WU-4 — /api/push/* endpoints.

Tests run BEFORE the implementation exists; they must all fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-5 (SC-5.1–5.7)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "push_test.db"
    apply_migrations(path, MIGRATIONS_DIR)
    # Seed a VAPID row so get() does not raise
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT OR IGNORE INTO vapid_keys (id, public_key, private_key, created_at)"
        " VALUES (1, 'BTestPublicKeyBase64urlValue', '-----BEGIN PRIVATE KEY-----\nfake', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def push_settings(db_path: Path, tmp_path: Path) -> Settings:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return Settings(db_path=db_path, projects_root=projects_root)


@pytest.fixture()
def push_app(push_settings: Settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: push_settings
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def client(push_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=push_app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/push/vapid-key
# ---------------------------------------------------------------------------


async def test_get_vapid_key_returns_200(client: AsyncClient) -> None:
    """GET /api/push/vapid-key returns 200 with a public_key field. (SC-5.1)"""
    response = await client.get("/api/push/vapid-key")
    assert response.status_code == 200


async def test_get_vapid_key_returns_public_key(client: AsyncClient) -> None:
    """GET /api/push/vapid-key returns JSON with non-empty public_key. (SC-5.2)"""
    response = await client.get("/api/push/vapid-key")
    assert response.status_code == 200
    data = response.json()
    assert "public_key" in data
    assert data["public_key"] == "BTestPublicKeyBase64urlValue"


# ---------------------------------------------------------------------------
# POST /api/push/subscribe
# ---------------------------------------------------------------------------


async def test_post_subscribe_new_returns_201(client: AsyncClient) -> None:
    """POST /api/push/subscribe with valid body returns 201. (SC-5.2)"""
    response = await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep1",
            "keys": {"p256dh": "key1", "auth": "auth1"},
        },
    )
    assert response.status_code == 201


async def test_post_subscribe_returns_subscribed_true(client: AsyncClient) -> None:
    """POST /api/push/subscribe returns {subscribed: true}."""
    response = await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep2",
            "keys": {"p256dh": "key2", "auth": "auth2"},
        },
    )
    assert response.status_code == 201
    assert response.json()["subscribed"] is True


async def test_post_subscribe_captures_user_agent(
    client: AsyncClient, db_path: Path
) -> None:
    """POST /api/push/subscribe captures User-Agent header in DB. (SC-5.4 related)"""
    response = await client.post(
        "/api/push/subscribe",
        headers={"User-Agent": "Mozilla/5.0 TestBrowser/1.0"},
        json={
            "endpoint": "https://fcm.googleapis.com/ep3",
            "keys": {"p256dh": "key3", "auth": "auth3"},
        },
    )
    assert response.status_code == 201
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT user_agent FROM push_subscriptions WHERE endpoint=?",
        ("https://fcm.googleapis.com/ep3",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert "Mozilla" in row[0]


async def test_post_subscribe_truncates_user_agent_to_80_chars(
    client: AsyncClient, db_path: Path
) -> None:
    """POST /api/push/subscribe truncates User-Agent to 80 chars server-side. (SC-5.4)"""
    long_ua = "A" * 200
    response = await client.post(
        "/api/push/subscribe",
        headers={"User-Agent": long_ua},
        json={
            "endpoint": "https://fcm.googleapis.com/ep-long-ua",
            "keys": {"p256dh": "klong", "auth": "along"},
        },
    )
    assert response.status_code == 201
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT user_agent FROM push_subscriptions WHERE endpoint=?",
        ("https://fcm.googleapis.com/ep-long-ua",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert len(row[0]) <= 80


async def test_post_subscribe_no_user_agent_stores_none(
    client: AsyncClient, db_path: Path
) -> None:
    """POST /api/push/subscribe without User-Agent header stores None."""
    response = await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep-no-ua",
            "keys": {"p256dh": "knua", "auth": "anua"},
        },
    )
    assert response.status_code == 201
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT user_agent FROM push_subscriptions WHERE endpoint=?",
        ("https://fcm.googleapis.com/ep-no-ua",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] is None


async def test_post_subscribe_invalid_body_returns_422(client: AsyncClient) -> None:
    """POST /api/push/subscribe with invalid body returns 422. (SC-5.10, REQ-5.10)"""
    response = await client.post(
        "/api/push/subscribe",
        json={"endpoint": "https://ep1"},  # missing keys
    )
    assert response.status_code == 422


async def test_post_subscribe_upsert_no_duplicate(
    client: AsyncClient, db_path: Path
) -> None:
    """Re-subscribe same endpoint returns 201 and does not create duplicate row. (SC-5.3)"""
    body = {
        "endpoint": "https://fcm.googleapis.com/ep-upsert",
        "keys": {"p256dh": "k1", "auth": "a1"},
    }
    await client.post("/api/push/subscribe", json=body)
    body["keys"] = {"p256dh": "k2", "auth": "a2"}
    response = await client.post("/api/push/subscribe", json=body)
    assert response.status_code == 201
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint=?",
        ("https://fcm.googleapis.com/ep-upsert",),
    ).fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# POST /api/push/unsubscribe
# ---------------------------------------------------------------------------


async def test_post_unsubscribe_found_returns_true(client: AsyncClient) -> None:
    """POST /api/push/unsubscribe with existing endpoint returns {unsubscribed: true}. (SC-5.5)"""
    await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep-unsub",
            "keys": {"p256dh": "k1", "auth": "a1"},
        },
    )
    response = await client.post(
        "/api/push/unsubscribe",
        json={"endpoint": "https://fcm.googleapis.com/ep-unsub"},
    )
    assert response.status_code == 200
    assert response.json()["unsubscribed"] is True


async def test_post_unsubscribe_not_found_returns_false(client: AsyncClient) -> None:
    """POST /api/push/unsubscribe with unknown endpoint returns {unsubscribed: false}. (SC-5.6)"""
    response = await client.post(
        "/api/push/unsubscribe",
        json={"endpoint": "https://fcm.googleapis.com/nonexistent"},
    )
    assert response.status_code == 200
    assert response.json()["unsubscribed"] is False


# ---------------------------------------------------------------------------
# GET /api/push/subscriptions
# ---------------------------------------------------------------------------


async def test_get_subscriptions_returns_list(client: AsyncClient) -> None:
    """GET /api/push/subscriptions returns {subscriptions: [...]}. (SC-5.7)"""
    await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep-list1",
            "keys": {"p256dh": "k1", "auth": "a1"},
        },
    )
    response = await client.get("/api/push/subscriptions")
    assert response.status_code == 200
    data = response.json()
    assert "subscriptions" in data
    assert len(data["subscriptions"]) >= 1


async def test_get_subscriptions_does_not_expose_crypto_keys(client: AsyncClient) -> None:
    """GET /api/push/subscriptions must NOT include p256dh or auth fields. (REQ-5.9 related)"""
    await client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/ep-nokeys",
            "keys": {"p256dh": "secret-p256dh", "auth": "secret-auth"},
        },
    )
    response = await client.get("/api/push/subscriptions")
    assert response.status_code == 200
    data = response.json()
    for sub in data["subscriptions"]:
        assert "p256dh" not in sub
        assert "auth" not in sub
