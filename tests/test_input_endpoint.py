"""Tests for POST /ui/instances/{id}/input — input endpoint — WU-5 (red).

Covers:
  - Happy path: returns 200 + HX-Trigger: input-sent header
  - Empty text: returns 400 with error fragment
  - Whitespace-only text: returns 400
  - Instance not found: returns 404
  - Adapter error: returns 500
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
def inp_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def inp_app(inp_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: inp_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def inp_client(inp_app):
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=inp_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(inp_settings, tmp_db):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


@pytest.fixture()
def instances_repo(inp_settings, tmp_db):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_input_happy_path(
    inp_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """POST /ui/instances/{id}/input with valid text → 200 + HX-Trigger: input-sent."""
    p_path = tmp_projects_root / "acme.com" / "inpproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="InpProj", slug="inpproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await inp_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    response = await inp_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "/sdd-continue", "send_enter": "true"},
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    assert "input-sent" in response.headers["HX-Trigger"]

    # Adapter recorded the send_keys call
    assert len(fake_adapter.sent_keys) > 0
    assert fake_adapter.sent_keys[-1][1] == "/sdd-continue"


async def test_input_empty_text_rejected(
    inp_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """POST with empty text → 400 error fragment."""
    p_path = tmp_projects_root / "acme.com" / "emptyinp"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="EmptyInp", slug="emptyinp", path=p_path, domain="acme.com"
        )
    )
    launch_resp = await inp_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    response = await inp_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "", "send_enter": "true"},
    )
    assert response.status_code == 400


async def test_input_whitespace_only_rejected(
    inp_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """POST with whitespace-only text → 400 error fragment."""
    p_path = tmp_projects_root / "acme.com" / "wsonly"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="WsOnly", slug="wsonly", path=p_path, domain="acme.com"
        )
    )
    launch_resp = await inp_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    response = await inp_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "   ", "send_enter": "true"},
    )
    assert response.status_code == 400


async def test_input_instance_not_found(
    inp_client: AsyncClient,
) -> None:
    """POST to nonexistent instance → 404."""
    response = await inp_client.post(
        "/ui/instances/nonexistent-id/input",
        data={"text": "hello", "send_enter": "true"},
    )
    assert response.status_code == 404


async def test_input_adapter_error_returns_non_5xx(
    inp_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Adapter TmuxOperationError → 4xx (non-5xx) HTML fragment per spec Never-5xx.

    RED: post_instance_input currently returns 500. This test asserts it must
    return a 4xx so the HTMX poll loop is never broken.
    """
    p_path = tmp_projects_root / "acme.com" / "adapterr"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="AdapterR", slug="adapterr", path=p_path, domain="acme.com"
        )
    )
    launch_resp = await inp_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Kill session so send_keys raises TmuxOperationError
    fake_adapter._sessions.pop(instance.tmux_session_name, None)

    response = await inp_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "hello", "send_enter": "true"},
    )
    assert response.status_code < 500, (
        f"Expected 4xx but got {response.status_code} — "
        "TmuxOperationError must never cause a 5xx (spec: Never 5xx)"
    )
    assert response.status_code >= 400
    # Response must be an HTML fragment (not a bare 5xx stacktrace)
    content_type = response.headers.get("content-type", "")
    assert "html" in content_type
