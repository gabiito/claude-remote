"""Red tests for /ui/* HTMX action routes — WU-6/WU-7.

Tests for:
  POST /ui/projects               — create project form
  POST /ui/projects/{id}/launch   — launch instance
  POST /ui/instances/{id}/stop    — stop instance
  DELETE /ui/projects/{id}        — delete project
  GET /static/css/app.css         — static file served
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

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
def ui_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def ui_app(ui_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: ui_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def ui_client(ui_app):
    async with AsyncClient(
        transport=ASGITransport(app=ui_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(ui_settings):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(ui_settings.db_path)
    )


@pytest.fixture()
def instances_repo(ui_settings):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(ui_settings.db_path)
    )


@pytest.fixture()
def existing_project(projects_repo, tmp_projects_root):
    """Create and return a real project in the DB."""
    path = tmp_projects_root / "example.com" / "myproj"
    path.mkdir(parents=True)
    return projects_repo.create(
        project_create=ProjectCreate(name="MyProj", slug="myproj", path=path, domain="example.com")
    )


# ---------------------------------------------------------------------------
# GET /static/css/app.css
# ---------------------------------------------------------------------------


async def test_app_css_served(ui_client: AsyncClient) -> None:
    """GET /static/css/app.css returns 200 with text/css content type."""
    response = await ui_client.get("/static/css/app.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# POST /ui/projects — create project
# ---------------------------------------------------------------------------


async def test_post_ui_projects_happy_path(
    ui_client: AsyncClient,
    tmp_projects_root,
) -> None:
    """POST /ui/projects with valid form data returns 200 with project card HTML."""
    path = tmp_projects_root / "example.com" / "newproj"
    path.mkdir(parents=True)

    response = await ui_client.post(
        "/ui/projects",
        data={"name": "New Project", "path": str(path)},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'class="project-card"' in response.text
    assert "data-project-id=" in response.text


async def test_post_ui_projects_missing_path(ui_client: AsyncClient) -> None:
    """POST /ui/projects without a path returns 400 with error fragment and HX headers."""
    response = await ui_client.post(
        "/ui/projects",
        data={"name": "No Path Project"},
    )
    assert response.status_code == 400
    assert "HX-Reswap" in response.headers
    assert response.headers["HX-Reswap"] == "innerHTML"
    assert "HX-Retarget" in response.headers
    assert response.headers["HX-Retarget"] == "#form-error"
    assert 'id="form-error"' in response.text
    assert 'class="error-message"' in response.text


async def test_post_ui_projects_invalid_path(
    ui_client: AsyncClient,
    tmp_projects_root,
) -> None:
    """POST /ui/projects with a path outside projects_root returns 400 with error fragment."""
    response = await ui_client.post(
        "/ui/projects",
        data={"name": "Bad Path", "path": "/tmp/not-under-root"},
    )
    assert response.status_code == 400
    assert response.headers.get("HX-Reswap") == "innerHTML"
    assert response.headers.get("HX-Retarget") == "#form-error"
    assert 'class="error-message"' in response.text


# ---------------------------------------------------------------------------
# POST /ui/projects/{id}/launch
# ---------------------------------------------------------------------------


async def test_post_ui_launch_happy_path(
    ui_client: AsyncClient,
    existing_project,
) -> None:
    """POST /ui/projects/{id}/launch returns 200 with updated project card."""
    response = await ui_client.post(f"/ui/projects/{existing_project.id}/launch")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'class="instance-row"' in response.text
    # Status pill should reflect starting or running state
    assert "status-pill" in response.text


async def test_post_ui_launch_not_found(ui_client: AsyncClient) -> None:
    """POST /ui/projects/bad-id/launch returns 404 error fragment with HX headers."""
    response = await ui_client.post("/ui/projects/nonexistent-id/launch")
    assert response.status_code == 404
    assert response.headers.get("HX-Reswap") == "innerHTML"
    assert response.headers.get("HX-Retarget") == "#form-error"
    assert 'class="error-message"' in response.text


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/stop
# ---------------------------------------------------------------------------


async def test_post_ui_stop_happy_path(
    ui_client: AsyncClient,
    existing_project,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """POST /ui/instances/{id}/stop returns 200 with updated instance row (stopped)."""
    # Launch first to create an instance
    launch_resp = await ui_client.post(f"/ui/projects/{existing_project.id}/launch")
    assert launch_resp.status_code == 200
    assert "data-instance-id=" in launch_resp.text

    # Extract the instance id from the HTML
    import re
    m = re.search(r'data-instance-id="([^"]+)"', launch_resp.text)
    assert m is not None, f"No data-instance-id in response: {launch_resp.text}"
    instance_id = m.group(1)

    response = await ui_client.post(f"/ui/instances/{instance_id}/stop")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "status-pill" in response.text
    assert "status-stopped" in response.text


async def test_post_ui_stop_not_found(ui_client: AsyncClient) -> None:
    """POST /ui/instances/bad-id/stop returns 404 error fragment with HX headers."""
    response = await ui_client.post("/ui/instances/nonexistent-id/stop")
    assert response.status_code == 404
    assert response.headers.get("HX-Reswap") == "innerHTML"
    assert response.headers.get("HX-Retarget") == "#form-error"
    assert 'class="error-message"' in response.text


# ---------------------------------------------------------------------------
# DELETE /ui/projects/{id}
# ---------------------------------------------------------------------------


async def test_delete_ui_project_happy_path(
    ui_client: AsyncClient,
    existing_project,
) -> None:
    """DELETE /ui/projects/{id} returns 200 with empty body."""
    response = await ui_client.delete(f"/ui/projects/{existing_project.id}")
    assert response.status_code == 200
    assert response.text.strip() == ""


async def test_delete_ui_project_not_found(ui_client: AsyncClient) -> None:
    """DELETE /ui/projects/nonexistent returns 404 error fragment with HX headers."""
    response = await ui_client.delete("/ui/projects/nonexistent-id")
    assert response.status_code == 404
    assert response.headers.get("HX-Reswap") == "innerHTML"
    assert response.headers.get("HX-Retarget") == "#form-error"
    assert 'class="error-message"' in response.text
