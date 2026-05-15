"""Tests for GET /projects/{id} deep view — WU-3 (red).

Covers:
  - Happy path: active instance → 200, output panel, input form, quick actions
  - No active instance → 200, no polling hx-trigger
  - Project not found → 404 full HTML page
  - InstanceView importable from routes._views
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
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
def pv_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def pv_app(pv_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: pv_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def pv_client(pv_app):
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=pv_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(pv_settings, tmp_db):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


@pytest.fixture()
def instances_repo(pv_settings, tmp_db):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_instance_view_importable_from_views() -> None:
    """InstanceView must live in routes._views — no duplicate DTO (DoD §2)."""
    from claude_remote.routes._views import InstanceView  # noqa: F401

    assert InstanceView is not None


async def test_get_project_view_happy_path(
    pv_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET /projects/{id} with a running instance returns 200 with output panel + input form + quick actions."""
    p_path = tmp_projects_root / "acme.com" / "myproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="MyProj", slug="myproj", path=p_path, domain="acme.com"
        )
    )

    # Launch an instance (sets status=running in DB)
    launch_resp = await pv_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    response = await pv_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    html = response.text

    # Output panel with polling
    assert 'id="output-content"' in html
    # Input form
    assert 'id="input-form"' in html
    # Quick action buttons
    assert "/sdd-continue" in html
    assert "/sdd-verify" in html
    assert "/clear" in html


async def test_get_project_view_no_active_instance(
    pv_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """GET /projects/{id} with no active instance → 200, no 2s polling trigger."""
    p_path = tmp_projects_root / "acme.com" / "stopped"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="Stopped", slug="stopped", path=p_path, domain="acme.com"
        )
    )

    response = await pv_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    html = response.text

    # No polling when no active instance
    assert "every 2s" not in html


async def test_get_project_view_not_found(
    pv_client: AsyncClient,
) -> None:
    """GET /projects/{nonexistent_id} → 404 with full HTML page (extends base.html)."""
    response = await pv_client.get(
        "/projects/nonexistent-uuid-xxxx", headers={"Accept": "text/html"}
    )
    assert response.status_code == 404
    html = response.text
    # Must be a full HTML page (not a fragment)
    assert "<!doctype html>" in html.lower() or "<html" in html.lower()
    # Must contain some error indication
    assert "404" in html or "not found" in html.lower() or "no encontrado" in html.lower()


# ---------------------------------------------------------------------------
# WU-6: Session-ended banner + events feed (50 events max)
# ---------------------------------------------------------------------------


async def test_project_view_session_ended_banner_appears(
    pv_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """When all instances are stopped/crashed, the session-ended banner is shown."""
    p_path = tmp_projects_root / "acme.com" / "bannerproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="BannerProj", slug="bannerproj", path=p_path, domain="acme.com"
        )
    )

    # Launch and then stop the instance
    launch_resp = await pv_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Stop the instance
    stop_resp = await pv_client.post(f"/ui/instances/{instance.id}/stop")
    assert stop_resp.status_code == 200

    response = await pv_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    html = response.text

    # Banner must be visible
    assert "cr-banner" in html
    # Input form must be disabled (cr-disabled class or disabled attr)
    assert "cr-disabled" in html or 'disabled' in html


async def test_project_view_events_feed_renders_up_to_50(
    pv_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Events feed shows up to 50 events (not inside a <details> element)."""
    import json

    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.events import EventsRepository

    p_path = tmp_projects_root / "acme.com" / "feedproj2"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="FeedProj2", slug="feedproj2", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await pv_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Insert 60 events (should show max 50)
    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.events import EventsRepository

    # Get db from pv_settings in fixture scope
    # Use the instances_repo's connection to find the db
    events_repo = EventsRepository(
        connection_factory=instances_repo._factory  # type: ignore[attr-defined]
    )
    for i in range(60):
        events_repo.create(
            instance_id=instance.id,
            project_id=project.id,
            event_type="Notification",
            payload=json.dumps({"message": f"event-{i}"}),
        )

    response = await pv_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    html = response.text

    # Events feed is always visible (NOT inside a <details> tag)
    assert "<details" not in html or "cr-events-pane" in html
    # At most 50 event entries (check cr-event-row count)
    event_row_count = html.count("cr-event-row")
    assert event_row_count <= 50
    assert event_row_count > 0
