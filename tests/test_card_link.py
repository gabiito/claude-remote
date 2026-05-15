"""Tests for home card anchor wrap and design layout classes — WU-7 (red).

Covers:
  - Home card has <a href="/projects/{id}"> wrapping the card body
  - Action buttons have @click.stop attribute
  - New design layout classes are present (cr-shell, cr-header, cr-card)
  - Status LED data attrs present (data-status on cr-led)
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
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
def cl_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def cl_app(cl_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: cl_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def cl_client(cl_app):
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=cl_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(cl_settings, tmp_db):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(tmp_db)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_home_card_has_anchor_link_to_project(
    cl_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Home card body is wrapped in <a href='/projects/{id}'> (REQ-C1)."""
    p_path = tmp_projects_root / "acme.com" / "linkproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="LinkProj", slug="linkproj", path=p_path, domain="acme.com"
        )
    )

    response = await cl_client.get("/")
    assert response.status_code == 200
    html = response.text

    # Must have an anchor pointing to the project view
    assert f'href="/projects/{project.id}"' in html


async def test_home_action_buttons_have_click_stop(
    cl_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Action buttons have @click.stop to prevent card navigation (REQ-C1)."""
    p_path = tmp_projects_root / "acme.com" / "stopproj"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="StopProj", slug="stopproj", path=p_path, domain="acme.com"
        )
    )

    response = await cl_client.get("/")
    assert response.status_code == 200
    html = response.text

    # @click.stop must be present on action buttons
    assert "@click.stop" in html


async def test_home_uses_cr_shell_layout(
    cl_client: AsyncClient,
) -> None:
    """Home page uses new cr-shell layout class (design system)."""
    response = await cl_client.get("/")
    assert response.status_code == 200
    assert "cr-shell" in response.text


async def test_home_uses_cr_header(
    cl_client: AsyncClient,
) -> None:
    """Home page uses cr-header class (design system)."""
    response = await cl_client.get("/")
    assert response.status_code == 200
    assert "cr-header" in response.text


async def test_home_card_uses_cr_card_class(
    cl_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Project card uses cr-card class from design system."""
    p_path = tmp_projects_root / "acme.com" / "cardclass"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="CardClass", slug="cardclass", path=p_path, domain="acme.com"
        )
    )

    response = await cl_client.get("/")
    assert response.status_code == 200
    assert "cr-card" in response.text


async def test_home_card_has_status_led(
    cl_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Project card has status LED element with data-status attribute (design system)."""
    p_path = tmp_projects_root / "acme.com" / "ledproj"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="LedProj", slug="ledproj", path=p_path, domain="acme.com"
        )
    )

    response = await cl_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "cr-led" in html
    assert "data-status=" in html


async def test_base_html_has_dark_color_scheme(
    cl_client: AsyncClient,
) -> None:
    """base.html has color-scheme dark meta tag (design system requirement)."""
    response = await cl_client.get("/")
    assert response.status_code == 200
    assert 'color-scheme' in response.text and 'dark' in response.text


async def test_base_html_loads_google_fonts(
    cl_client: AsyncClient,
) -> None:
    """base.html loads Google Fonts (Inter + JetBrains Mono) for design system."""
    response = await cl_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "fonts.googleapis.com" in html or "Inter" in html
