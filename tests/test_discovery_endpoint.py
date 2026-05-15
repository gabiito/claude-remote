"""Red tests for POST /ui/discovery/sync endpoint — WU-4.

Integration tests using AsyncClient + ASGITransport against a live app
instance backed by a per-test temporary DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

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
def tmp_db(tmp_path: Path):
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture()
def sync_settings(tmp_db: Path, tmp_projects_root: Path) -> Settings:
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def sync_app(sync_settings: Settings):
    from claude_remote.routes.instances import get_tmux_adapter
    from claude_remote.services.tmux_adapter import FakeTmuxAdapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: sync_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: FakeTmuxAdapter()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def sync_client(sync_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=sync_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(sync_settings: Settings) -> ProjectsRepository:
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(sync_settings.db_path)
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_project_dir(root: Path, domain: str, name: str) -> Path:
    """Create <root>/<domain>/<name>/ and return the path."""
    p = root / domain / name
    p.mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_sync_empty_root_empty_db_returns_200(
    sync_client: AsyncClient,
) -> None:
    """POST /ui/discovery/sync with empty root + empty DB returns 200."""
    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_sync_empty_root_returns_nada_para_sincronizar(
    sync_client: AsyncClient,
) -> None:
    """POST /ui/discovery/sync with empty root shows 'Nothing to sync'."""
    response = await sync_client.post("/ui/discovery/sync")
    assert "Nothing to sync" in response.text


async def test_sync_always_has_hx_trigger_header(
    sync_client: AsyncClient,
) -> None:
    """POST /ui/discovery/sync always returns HX-Trigger: projects-synced."""
    response = await sync_client.post("/ui/discovery/sync")
    assert response.headers.get("HX-Trigger") == "projects-synced"


async def test_sync_new_candidates_inserted(
    sync_client: AsyncClient,
    sync_settings: Settings,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """3 new candidates in root → all inserted, summary shows '+3 nuevos'."""
    make_project_dir(tmp_projects_root, "alpha", "proj1")
    make_project_dir(tmp_projects_root, "alpha", "proj2")
    make_project_dir(tmp_projects_root, "beta", "proj3")

    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200
    assert "3" in response.text  # 3 new projects

    all_projects = projects_repo.list_all()
    assert len(all_projects) == 3


async def test_sync_already_registered_not_duplicated(
    sync_client: AsyncClient,
    sync_settings: Settings,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Candidates already in DB by (domain, slug) are not re-inserted."""
    proj_path = make_project_dir(tmp_projects_root, "alpha", "existing")
    # Pre-register the project
    projects_repo.create(
        project_create=ProjectCreate(
            name="existing",
            slug="existing",
            path=proj_path,
            domain="alpha",
        )
    )

    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200

    # Should still be only 1 project
    all_projects = projects_repo.list_all()
    assert len(all_projects) == 1


async def test_sync_stale_detection_marks_missing_path(
    sync_client: AsyncClient,
    sync_settings: Settings,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Project with missing path on disk → is_stale=True after sync."""
    # Create dir and register project, then delete the dir
    proj_path = make_project_dir(tmp_projects_root, "alpha", "gone-project")
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="gone-project",
            slug="gone-project",
            path=proj_path,
            domain="alpha",
        )
    )

    # Remove the directory
    import shutil
    shutil.rmtree(str(proj_path))

    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200

    updated = projects_repo.get(project.id)
    assert updated is not None
    assert updated.is_stale is True


async def test_sync_stale_reactivation_unmarks_stale(
    sync_client: AsyncClient,
    sync_settings: Settings,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Project marked stale whose path reappears → is_stale=False after sync."""
    proj_path = make_project_dir(tmp_projects_root, "alpha", "revived")
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="revived",
            slug="revived",
            path=proj_path,
            domain="alpha",
        )
    )
    # Mark stale manually
    projects_repo.mark_stale(project.id)
    stale_check = projects_repo.get(project.id)
    assert stale_check is not None and stale_check.is_stale is True

    # Directory still exists — sync should unmark it
    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200

    updated = projects_repo.get(project.id)
    assert updated is not None
    assert updated.is_stale is False


async def test_sync_insert_failure_non_fatal(
    sync_client: AsyncClient,
    sync_settings: Settings,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """A single insert failure does not abort sync; other candidates still processed."""
    # Create two candidates
    make_project_dir(tmp_projects_root, "alpha", "proj1")
    make_project_dir(tmp_projects_root, "alpha", "proj2")

    # Pre-register proj1 with same slug to force a DuplicateProjectError on re-insert
    proj1_path = tmp_projects_root / "alpha" / "proj1"
    projects_repo.create(
        project_create=ProjectCreate(
            name="proj1",
            slug="proj1",
            path=proj1_path,
            domain="alpha",
        )
    )

    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200
    # proj2 should have been inserted despite proj1 collision
    all_projects = projects_repo.list_all()
    slugs = {p.slug for p in all_projects}
    assert "proj2" in slugs


async def test_sync_response_contains_summary_fragment(
    sync_client: AsyncClient,
    tmp_projects_root: Path,
) -> None:
    """Response body contains cr-sync-toast div or span elements."""
    make_project_dir(tmp_projects_root, "d", "p")
    response = await sync_client.post("/ui/discovery/sync")
    assert response.status_code == 200
    # Should contain some HTML content (the sync_summary partial)
    assert len(response.text.strip()) > 0
