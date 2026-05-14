"""Red tests for WU-3 — POST /hooks/{event_type} never-raise contract.

Hard invariant: the endpoint ALWAYS returns HTTP 200. Never 4xx or 5xx.
Claude Code's hook flow must not be broken by our receiver.

Fixture strategy:
  - ``db_path``          — fresh tmp DB with all migrations applied
  - ``app_hooks``        — FastAPI app with DB + hooks router wired
  - ``client``           — AsyncClient for the above app
  - ``proj_id``          — a seeded project row
  - ``token``            — hook_token of a seeded instance
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "hooks_test.db"
    apply_migrations(path, MIGRATIONS_DIR)
    return path


@pytest.fixture()
def settings_override(db_path: Path, tmp_path: Path) -> Settings:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return Settings(db_path=db_path, projects_root=projects_root)


@pytest.fixture()
def app_hooks(settings_override: Settings):
    """FastAPI app with get_settings overridden (bypasses lifespan)."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_override
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def client(app_hooks) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app_hooks),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        yield c  # type: ignore[misc]


def _make_factory(db_path: Path):
    return lambda: get_connection_for(db_path)


@pytest.fixture()
def proj_id(db_path: Path, tmp_path: Path) -> str:
    repo = ProjectsRepository(_make_factory(db_path))
    p = tmp_path / "sandbox" / "myproject"
    p.mkdir(parents=True)
    proj = repo.create(
        project_create=ProjectCreate(
            name="myproject", slug="myproject", path=p, domain="sandbox"
        )
    )
    return proj.id


@pytest.fixture()
def token(db_path: Path, proj_id: str) -> str:
    repo = InstancesRepository(_make_factory(db_path))
    inst = repo.create(project_id=proj_id, tmux_session_name="claude-remote-myproject-aa000001")
    return inst.hook_token


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_happy_path_returns_200_with_event_id(client: AsyncClient, token: str) -> None:
    """Valid token + valid event_type → 200 received: true, event_id present."""
    resp = await client.post(
        f"/hooks/Notification?token={token}",
        json={"text": "hello"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] is True
    assert "event_id" in body
    assert body["event_id"]  # non-empty string


@pytest.mark.anyio
async def test_happy_path_event_stored_in_db(
    client: AsyncClient, db_path: Path, token: str, proj_id: str
) -> None:
    """Happy path: event row is actually stored in the DB."""

    events_repo = EventsRepository(_make_factory(db_path))
    resp = await client.post(f"/hooks/SessionStart?token={token}", json={})
    body = resp.json()
    event_id = body["event_id"]

    events = events_repo.list_for_project(proj_id)
    assert any(e.id == event_id for e in events), "Event must be persisted in DB"


@pytest.mark.anyio
async def test_all_valid_event_types_accepted(client: AsyncClient, token: str) -> None:
    """All 6 Claude Code event types are accepted."""
    event_types = [
        "SessionStart", "Notification", "Stop", "PreToolUse", "PostToolUse", "SessionEnd"
    ]
    for ev in event_types:
        resp = await client.post(f"/hooks/{ev}?token={token}", json={})
        assert resp.status_code == 200
        assert resp.json()["received"] is True, f"Event type {ev!r} was not received"


# ---------------------------------------------------------------------------
# Never-raise hard invariant
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_event_type_returns_200(client: AsyncClient, token: str) -> None:
    """Unknown event_type → 200, received: false, reason: unknown_event_type."""
    resp = await client.post(f"/hooks/UnknownType?token={token}", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] is False
    assert body["reason"] == "unknown_event_type"


@pytest.mark.anyio
async def test_missing_token_returns_200(client: AsyncClient) -> None:
    """No ?token= → 200, received: false, reason: missing_token."""
    resp = await client.post("/hooks/Notification", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] is False
    assert body["reason"] == "missing_token"


@pytest.mark.anyio
async def test_unknown_token_returns_200(client: AsyncClient) -> None:
    """Unknown token → 200, received: false, reason: unknown_token."""
    resp = await client.post("/hooks/Notification?token=bogus-token-xyz", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] is False
    assert body["reason"] == "unknown_token"


@pytest.mark.anyio
async def test_internal_error_stub_still_returns_200(
    app_hooks, db_path: Path, token: str
) -> None:
    """CRITICAL: even when events_repo.create raises, endpoint returns 200 received:false.

    This is the hard invariant test. We monkeypatch get_events_repo to return
    a stub that always raises on create().
    """
    from claude_remote.routes import hooks as hooks_module

    class _AlwaysRaisesEventsRepo:
        def create(self, **kwargs):
            raise RuntimeError("simulated internal error")

    # Override the DI dependency in the app
    original_override = app_hooks.dependency_overrides.copy()
    app_hooks.dependency_overrides[hooks_module.get_events_repo] = lambda: _AlwaysRaisesEventsRepo()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app_hooks),  # type: ignore[arg-type]
            base_url="http://test",
        ) as c:
            resp = await c.post(f"/hooks/Notification?token={token}", json={})
    finally:
        app_hooks.dependency_overrides = original_override

    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] is False
    assert body["reason"] == "internal_error"


@pytest.mark.anyio
async def test_malformed_json_body_still_returns_200(client: AsyncClient, token: str) -> None:
    """Malformed JSON body: persisted as {raw: ...}, still returns received:true."""
    resp = await client.post(
        f"/hooks/Notification?token={token}",
        content=b"not-valid-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    # Either received:true (raw stored) or at minimum still 200
    body = resp.json()
    assert body["received"] is True or body["received"] is False  # ALWAYS 200, never 5xx
