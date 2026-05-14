"""Red tests for GET / home page — WU-6/WU-7.

These tests must FAIL until home.py + home.html are implemented.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository

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
def home_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def home_app(home_settings, tmp_db):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: home_settings
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def home_client(home_app):
    from httpx import ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=home_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(home_settings):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(home_settings.db_path)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_home_returns_200(home_client: AsyncClient) -> None:
    """GET / returns 200 with HTML content type."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_home_contains_header(home_client: AsyncClient) -> None:
    """Home page contains the 'claude-remote' header."""
    response = await home_client.get("/")
    assert "claude-remote" in response.text


async def test_home_contains_stylesheet_link(home_client: AsyncClient) -> None:
    """Home page loads app.css stylesheet."""
    response = await home_client.get("/")
    assert "/static/css/app.css" in response.text


async def test_home_contains_alpine_js(home_client: AsyncClient) -> None:
    """Home page loads Alpine.js CDN."""
    response = await home_client.get("/")
    assert "alpinejs" in response.text


async def test_home_contains_htmx(home_client: AsyncClient) -> None:
    """Home page loads HTMX CDN."""
    response = await home_client.get("/")
    assert "htmx.org" in response.text


async def test_home_empty_state(home_client: AsyncClient) -> None:
    """Home page shows empty state message when no projects registered."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "No hay proyectos" in response.text


async def test_home_lists_projects(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Home page lists registered projects with project-card elements."""
    # Create two fake project directories and register them
    p1_path = tmp_projects_root / "example.com" / "alpha"
    p1_path.mkdir(parents=True)
    p2_path = tmp_projects_root / "example.com" / "beta"
    p2_path.mkdir(parents=True)

    projects_repo.create(
        project_create=ProjectCreate(name="Alpha", slug="alpha", path=p1_path, domain="example.com")
    )
    projects_repo.create(
        project_create=ProjectCreate(name="Beta", slug="beta", path=p2_path, domain="example.com")
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    assert "Alpha" in response.text
    assert "Beta" in response.text
    assert 'class="project-card"' in response.text
    assert "data-project-id=" in response.text


async def test_home_project_card_has_data_id(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Each project card has a data-project-id attribute."""
    p_path = tmp_projects_root / "acme.com" / "myproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(name="MyProj", slug="myproj", path=p_path, domain="acme.com")
    )

    response = await home_client.get("/")
    assert f'data-project-id="{project.id}"' in response.text
