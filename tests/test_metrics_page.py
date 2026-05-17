"""GET /metrics page + /metrics/poll fragment + header entry (roadmap #3 WU-3)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository

pytestmark = pytest.mark.anyio


@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path):
    r = tmp_path / "p"
    r.mkdir()
    return r


@pytest.fixture()
def st(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def app(st):
    a = create_app()
    a.dependency_overrides[get_settings] = lambda: st
    yield a
    a.dependency_overrides.clear()


@pytest.fixture()
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(st, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


async def test_metrics_page_renders_sections(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "Metrics" in html
    assert "HOST" in html
    assert "ACTIVE SESSIONS" in html
    assert "APP" in html
    # Live host data wired (cpu/ram/disk labels present).
    assert "USAGE" in html or "CPU" in html
    assert "RAM" in html
    assert "DISK" in html


async def test_metrics_poll_fragment(client: AsyncClient) -> None:
    resp = await client.get("/metrics/poll")
    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body  # fragment, not a full page
    assert "HOST" in body and "APP" in body


async def test_metrics_page_app_section_counts(
    client: AsyncClient, projects_repo: ProjectsRepository, tmp_projects_root
) -> None:
    (tmp_projects_root / "wooli" / "m1").mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="m1", slug="m1", path=tmp_projects_root / "wooli" / "m1", domain="wooli"
        )
    )
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "projects" in resp.text  # APP sessions block


async def test_home_header_links_to_metrics(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'href="/metrics"' in resp.text
