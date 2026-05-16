"""POST /ui/instances/{id}/resize — fit-to-screen tmux resize (WU-B).

The deep view sends the measured terminal columns/rows; the endpoint resizes
the tmux window so the pane program re-renders at that width. Fire-and-forget
from the client — must never 5xx (404 only when the instance is missing).
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
def rz_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def rz_app(rz_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: rz_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def rz_client(rz_app):
    async with AsyncClient(
        transport=ASGITransport(app=rz_app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(rz_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(rz_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


async def _launch(rz_client, projects_repo, instances_repo, root):
    (root / "wooli" / "rz").mkdir(parents=True, exist_ok=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="rz", slug="rz", path=root / "wooli" / "rz", domain="wooli"
        )
    )
    await rz_client.post(f"/ui/projects/{project.id}/launch")
    return instances_repo.list_by_project(project.id)[0]


async def test_resize_happy_path(
    rz_client, projects_repo, instances_repo, tmp_projects_root, fake_adapter
) -> None:
    inst = await _launch(rz_client, projects_repo, instances_repo, tmp_projects_root)
    resp = await rz_client.post(
        f"/ui/instances/{inst.id}/resize", data={"cols": "52", "rows": "30"}
    )
    assert resp.status_code == 200
    assert fake_adapter.resizes
    sess, cols, rows = fake_adapter.resizes[-1]
    assert (cols, rows) == (52, 30)


async def test_resize_instance_not_found(rz_client) -> None:
    resp = await rz_client.post(
        "/ui/instances/nope/resize", data={"cols": "80", "rows": "24"}
    )
    assert resp.status_code == 404


async def test_resize_clamps_out_of_range(
    rz_client, projects_repo, instances_repo, tmp_projects_root, fake_adapter
) -> None:
    """Absurd values are clamped to sane tmux bounds, not passed through."""
    inst = await _launch(rz_client, projects_repo, instances_repo, tmp_projects_root)
    resp = await rz_client.post(
        f"/ui/instances/{inst.id}/resize", data={"cols": "5", "rows": "99999"}
    )
    assert resp.status_code == 200
    _, cols, rows = fake_adapter.resizes[-1]
    assert 20 <= cols <= 500
    assert 5 <= rows <= 400


async def test_resize_never_5xx_on_adapter_error(
    rz_client, projects_repo, instances_repo, tmp_projects_root, fake_adapter
) -> None:
    """Adapter raising must not bubble a 5xx (fire-and-forget cosmetic call)."""
    inst = await _launch(rz_client, projects_repo, instances_repo, tmp_projects_root)

    def _boom(*_a, **_k):
        from claude_remote.services.exceptions import TmuxOperationError

        raise TmuxOperationError("resize_window", RuntimeError("x"))

    fake_adapter.resize_window = _boom  # type: ignore[method-assign]
    resp = await rz_client.post(
        f"/ui/instances/{inst.id}/resize", data={"cols": "80", "rows": "24"}
    )
    assert resp.status_code == 200
