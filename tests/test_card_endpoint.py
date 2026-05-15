"""Red tests for GET /ui/projects/{id}/card — WU-3.

Tests FAIL until the card endpoint is implemented in routes/ui.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.services.tmux_adapter import FakeTmuxAdapter

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture()
def card_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def card_app(card_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: card_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def card_client(card_app):
    async with AsyncClient(
        transport=ASGITransport(app=card_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(card_settings):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(card_settings.db_path)
    )


@pytest.fixture()
def instances_repo(card_settings):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(card_settings.db_path)
    )


@pytest.fixture()
def events_repo(card_settings):
    return EventsRepository(
        connection_factory=lambda: get_connection_for(card_settings.db_path)
    )


@pytest.fixture()
def existing_project(projects_repo, tmp_projects_root):
    path = tmp_projects_root / "example.com" / "cardproj"
    path.mkdir(parents=True)
    return projects_repo.create(
        project_create=ProjectCreate(
            name="CardProj", slug="cardproj", path=path, domain="example.com"
        )
    )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ts(delta_seconds: float) -> str:
    """Return ISO 8601 UTC string for now - delta_seconds."""
    return (datetime.now(UTC) - timedelta(seconds=delta_seconds)).isoformat()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_card_happy_path_returns_200(
    card_client: AsyncClient,
    existing_project,
) -> None:
    """GET /ui/projects/{id}/card returns 200 with project-card HTML."""
    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'class="project-card"' in response.text
    assert f'data-project-id="{existing_project.id}"' in response.text


async def test_card_404_for_missing_project(card_client: AsyncClient) -> None:
    """GET /ui/projects/nonexistent/card returns 404 with HX-Reswap header."""
    response = await card_client.get("/ui/projects/nonexistent-id/card")
    assert response.status_code == 404
    assert "HX-Reswap" in response.headers


async def test_card_live_status_pretooluse(
    card_client: AsyncClient,
    existing_project,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Instance with recent PreToolUse shows data-live-status=active."""
    # Launch an instance via the API to create a real DB row
    launch_resp = await card_client.post(
        f"/ui/projects/{existing_project.id}/launch"
    )
    assert launch_resp.status_code == 200

    # Get the instance from DB
    instances = instances_repo.list_by_project(existing_project.id)
    assert len(instances) == 1
    instance = instances[0]

    # Insert a recent PreToolUse event
    events_repo.create(
        instance_id=instance.id,
        project_id=existing_project.id,
        event_type="PreToolUse",
        payload=json.dumps({"tool_name": "Bash"}),
    )

    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert 'data-live-status="active"' in response.text


async def test_card_live_status_needs_input(
    card_client: AsyncClient,
    existing_project,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Instance with recent Notification (no tool after) → needs_input."""
    launch_resp = await card_client.post(
        f"/ui/projects/{existing_project.id}/launch"
    )
    assert launch_resp.status_code == 200

    instances = instances_repo.list_by_project(existing_project.id)
    instance = instances[0]

    # Only a Notification, no tool after it
    events_repo.create(
        instance_id=instance.id,
        project_id=existing_project.id,
        event_type="Notification",
        payload=json.dumps({"message": "Approve?"}),
    )

    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert 'data-live-status="needs_input"' in response.text


async def test_card_events_feed_visible_when_events_exist(
    card_client: AsyncClient,
    existing_project,
    events_repo: EventsRepository,
    instances_repo: InstancesRepository,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Events feed <details> appears when project has recent events."""
    launch_resp = await card_client.post(
        f"/ui/projects/{existing_project.id}/launch"
    )
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(existing_project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=existing_project.id,
        event_type="Notification",
        payload=json.dumps({"message": "Hello"}),
    )

    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert 'class="events-feed"' in response.text


async def test_card_events_feed_hidden_when_no_events(
    card_client: AsyncClient,
    existing_project,
) -> None:
    """No events → events feed <details> is absent from the card."""
    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert 'class="events-feed"' not in response.text


async def test_card_has_htmx_polling_attrs(
    card_client: AsyncClient,
    existing_project,
) -> None:
    """Card response includes HTMX polling attributes (hx-trigger)."""
    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert "hx-trigger" in response.text
    assert "every 5s" in response.text


async def test_card_has_hx_preserve_on_details(
    card_client: AsyncClient,
    existing_project,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Events feed <details> has stable id and hx-preserve attribute."""
    launch_resp = await card_client.post(
        f"/ui/projects/{existing_project.id}/launch"
    )
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(existing_project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=existing_project.id,
        event_type="Notification",
        payload=json.dumps({"message": "test"}),
    )

    response = await card_client.get(f"/ui/projects/{existing_project.id}/card")
    assert response.status_code == 200
    assert f'id="events-feed-{existing_project.id}"' in response.text
    assert "hx-preserve" in response.text
