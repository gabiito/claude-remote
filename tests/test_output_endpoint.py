"""Tests for GET /ui/instances/{id}/output — output fragment endpoint — WU-4 (red).

Covers:
  - Happy path: returns <pre id="output-content"> with pane text
  - Instance not found: 404 with HX-Reswap header
  - Adapter error (session gone): 200 with fallback message (NEVER 5xx)
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
def out_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def out_app(out_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: out_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def out_client(out_app):
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=out_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(out_settings, tmp_db):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


@pytest.fixture()
def instances_repo(out_settings, tmp_db):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_output_happy_path(
    out_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET /ui/instances/{id}/output returns 200 with pre#output-content containing pane text."""
    p_path = tmp_projects_root / "acme.com" / "outproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="OutProj", slug="outproj", path=p_path, domain="acme.com"
        )
    )

    # Launch an instance (creates tmux session in fake adapter)
    launch_resp = await out_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Set pane content in fake adapter
    fake_adapter.set_pane_content(instance.tmux_session_name, "Claude output text")

    response = await out_client.get(f"/ui/instances/{instance.id}/output")
    assert response.status_code == 200
    html = response.text
    assert 'id="output-content"' in html
    assert "Claude output text" in html


async def test_output_instance_not_found(
    out_client: AsyncClient,
) -> None:
    """GET /ui/instances/{nonexistent}/output → 404 with HX-Reswap header."""
    response = await out_client.get("/ui/instances/nonexistent-id/output")
    assert response.status_code == 404
    assert "HX-Reswap" in response.headers


async def test_output_adapter_error_returns_200(
    out_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Adapter TmuxOperationError → 200 with fallback message (NEVER 5xx) — REQ-P2."""
    p_path = tmp_projects_root / "acme.com" / "errproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="ErrProj", slug="errproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await out_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Remove the session from fake adapter so capture_pane raises TmuxOperationError
    fake_adapter._sessions.pop(instance.tmux_session_name, None)

    response = await out_client.get(f"/ui/instances/{instance.id}/output")
    assert response.status_code == 200  # MUST NOT be 5xx
    html = response.text
    # Must contain a fallback message inside the pre element
    assert (
        "unavailable" in html.lower()
        or "no disponible" in html.lower()
        or "sesión" in html.lower()
    )
    assert 'id="output-content"' in html
