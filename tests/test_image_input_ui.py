"""Template-level tests for Slice 2 image-input frontend controls.

These tests assert rendered HTML structure and CSS class presence.
Real browser behaviors (actual clipboard read, real drag events, fetch() execution,
FormData submission) are ON-DEVICE-ONLY and are NOT faked here.

ON-DEVICE items (must be verified by operator before merging Slice 2):
  6.1 Upload a real image via the paperclip picker; confirm Claude Code receives it.
  6.2 If 6.1 fails, flip IMAGE_PATH_TEMPLATE to "@{path}" in services/image_upload.py.
  6.3 Android paste from clipboard (may degrade to picker/drag-drop on HTTP).
  6.4 Mobile drag-drop: drop outline appears, image is sent.
"""

from __future__ import annotations

from pathlib import Path

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

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "claude_remote"


# ---------------------------------------------------------------------------
# Fixtures (mirror test_projects_view.py style)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path: Path):
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
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=ui_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(ui_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(ui_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


# ---------------------------------------------------------------------------
# Helper: create a project + running instance, return (project, html)
# ---------------------------------------------------------------------------


async def _launch_and_get_html(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
    slug: str = "imgproj",
) -> tuple:
    p_path = tmp_projects_root / "test.com" / slug
    p_path.mkdir(parents=True, exist_ok=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name=slug, slug=slug, path=p_path, domain="test.com"
        )
    )
    launch_resp = await ui_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    resp = await ui_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 200
    return project, resp.text


# ---------------------------------------------------------------------------
# 5.1 RED — image controls present in project_view with running instance
# ---------------------------------------------------------------------------


async def test_attach_button_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html renders a button with class cr-attach when instance is running."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach1")
    assert "cr-attach" in html, "Expected .cr-attach button in project_view with running instance"


async def test_file_input_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html has a hidden file input accepting images when instance is running."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach2")
    assert 'type="file"' in html, "Expected hidden file input in project_view"
    assert 'accept="image/*"' in html, 'Expected accept="image/*" on file input'


async def test_paste_handler_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html textarea has a @paste handler (template level — JS not executed)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach3")
    assert "@paste" in html, "Expected @paste Alpine handler on textarea"


async def test_drop_handler_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html input dock has @drop handler for drag-drop."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach4")
    assert "@drop" in html, "Expected @drop Alpine handler on form/dock"


async def test_upload_image_url_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html contains the upload-image endpoint URL for the instance."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach5")
    assert "upload-image" in html, "Expected upload-image endpoint URL in project_view"


async def test_filereader_data_uri_wiring_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html uses FileReader/readAsDataURL for preview (no blob: URI)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach6")
    assert "FileReader" in html, "Expected FileReader in project_view Alpine code"
    assert "readAsDataURL" in html, "Expected readAsDataURL in project_view Alpine code"


async def test_no_blob_uri_in_preview_code(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html must NOT use createObjectURL or blob: for preview (CSP violation)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach7")
    assert "createObjectURL" not in html, "createObjectURL found — CSP violation: use data: URI"
    assert "blob:" not in html, "blob: URI found — blocked by CSP img-src 'self' data:"


async def test_preview_container_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html has an Alpine x-if preview container with cr-img-preview class."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach8")
    assert "cr-img-preview" in html, "Expected cr-img-preview class for thumbnail preview"
    assert "preview" in html, "Expected Alpine 'preview' data property in template"


async def test_drag_over_class_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html dock form has :class binding for cr-drag-over state."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach9")
    assert "cr-drag-over" in html, "Expected cr-drag-over in :class binding on form"
    assert "dragging" in html, "Expected dragging Alpine property for drag state"


# ---------------------------------------------------------------------------
# 5.1 RED — CSS classes present in app.css
# ---------------------------------------------------------------------------


def test_css_has_cr_attach_class() -> None:
    """app.css defines .cr-attach button style."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-attach" in css, ".cr-attach CSS class missing from app.css"


def test_css_has_cr_drag_over_class() -> None:
    """app.css defines .cr-drag-over dashed outline for drag state."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-drag-over" in css, ".cr-drag-over CSS class missing from app.css"


def test_css_has_cr_img_preview_class() -> None:
    """app.css defines .cr-img-preview thumbnail style."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-img-preview" in css, ".cr-img-preview CSS class missing from app.css"


# ---------------------------------------------------------------------------
# 5.1 RED — graceful degradation: no image controls when no active instance
# ---------------------------------------------------------------------------


async def test_no_attach_button_when_no_instance(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html WITHOUT a running instance must not render the cr-attach button
    (the entire #input-form is the disabled variant — cr-disabled)."""
    p_path = tmp_projects_root / "test.com" / "noinstance"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="noinstance", slug="noinstance", path=p_path, domain="test.com"
        )
    )
    resp = await ui_client.get(
        f"/projects/{project.id}", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 200
    html = resp.text
    assert "cr-disabled" in html, "Expected cr-disabled dock when no active instance"
    assert "cr-attach" not in html, "cr-attach must not render when no running instance"
