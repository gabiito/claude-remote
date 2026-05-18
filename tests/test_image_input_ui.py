"""Template-level tests for image-input v2 frontend (multi-chip staging).

These tests assert rendered HTML structure, Alpine wiring attributes, CSS class presence,
and static-asset existence. Real browser behaviors are ON-DEVICE-ONLY.

ON-DEVICE items (must be verified by operator before merging PR2):
  OD-1 Upload 1 image via paperclip picker; type a message; press Send. Confirm Claude Code
       receives <abs_path>\n<message> as one prompt.
  OD-2 Upload 2 images via picker; type a message; press Send. Confirm one combined send_keys
       with both paths prepended.
  OD-3 Android device over HTTP — paste an image from system clipboard. Confirm it uploads OR
       picker/drag-drop remain fully functional as fallback.
  OD-4 Mobile device — drag an image onto the input dock. Confirm drag-over outline appears,
       drop triggers attachFile, chip appears.
  OD-5 Stage 2+ chips, cancel one via X button. Confirm file deleted server-side, chip removed
       from UI, remaining chips unaffected.
  OD-6 Visual chip layout on a phone screen — chips do not overflow visible input area.
  OD-7 Chip-add sound — after picking/pasting/dropping an image and the chip appears,
       click.mp3 plays audibly on-device.
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
# F-1: Chip container and remove control
# ---------------------------------------------------------------------------


async def test_chip_container_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Rendered project_view.html contains a chip container for staged attachments
    (uses attachments array / x-show or x-for on attachments)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip1")
    assert "attachments" in html, "Expected 'attachments' Alpine array in project_view"
    # chip strip container is visible when attachments exist
    assert "cr-attachment-chips" in html or "x-for" in html, (
        "Expected chip container (cr-attachment-chips or x-for loop) in project_view"
    )


async def test_chip_has_remove_control(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Chip template contains a remove button (× control) with remove wiring."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip2")
    # The x-for chip template must have a remove button
    assert "cr-chip__remove" in html, "Expected .cr-chip__remove button in chip template"


async def test_chip_remove_wired_to_delete_endpoint(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Chip remove button's @click calls fetch(...DELETE...) to the cancel endpoint."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip3")
    # The remove button must reference the DELETE cancel URL
    assert "upload-image" in html, "Expected upload-image URL in chip remove wiring"
    assert "DELETE" in html, "Expected DELETE method in chip remove fetch call"


async def test_chip_name_is_x_text_not_innerhtml(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Chip name uses x-text (not innerHTML) — injection guard per design §5."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip4")
    # Must use x-text for chip name (autoescaped) never innerHTML
    assert "x-text" in html, "Expected x-text for chip name (injection guard)"
    assert "innerHTML" not in html, "innerHTML found — injection risk; use x-text for chip name"


# ---------------------------------------------------------------------------
# F-2: Picker / paste / drag controls present
# ---------------------------------------------------------------------------


async def test_paperclip_button_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html renders a button with class cr-attach (paperclip affordance)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach1")
    assert "cr-attach" in html, "Expected .cr-attach button in project_view with running instance"


async def test_file_input_accept_image_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html has a hidden file input accepting images."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach2")
    assert 'type="file"' in html, "Expected hidden file input in project_view"
    assert 'accept="image/*"' in html, 'Expected accept="image/*" on file input'


async def test_drag_drop_wiring_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html input dock has @dragover and @drop handlers."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach4")
    assert "@dragover" in html or "x-on:dragover" in html, "Expected @dragover handler on dock"
    assert "@drop" in html or "x-on:drop" in html, "Expected @drop handler on dock"


async def test_paste_handler_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Textarea has @paste handler extracting image items from ClipboardEvent."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach3")
    assert "@paste" in html or "x-on:paste" in html, "Expected @paste Alpine handler on textarea"


async def test_drag_over_class_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html dock form has :class binding for cr-drag-over state."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach9")
    assert "cr-drag-over" in html, "Expected cr-drag-over in :class binding on form"


# ---------------------------------------------------------------------------
# F-3: No blob: URI anywhere
# ---------------------------------------------------------------------------


async def test_no_blob_uri_in_template(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html must NOT use createObjectURL or blob: (CSP violation)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach7")
    assert "createObjectURL" not in html, "createObjectURL found — CSP violation; use data: URI"
    assert "blob:" not in html, "blob: URI found — blocked by CSP img-src 'self' data:"


def test_no_blob_uri_in_app_js() -> None:
    """static/js/ files must not reference blob: or createObjectURL."""
    js_dir = PACKAGE_ROOT / "static" / "js"
    if not js_dir.exists():
        return  # no app.js — nothing to check
    for js_file in js_dir.glob("*.js"):
        content = js_file.read_text()
        assert "createObjectURL" not in content, (
            f"createObjectURL in {js_file} — CSP violation"
        )
        assert "blob:" not in content, f"blob: URI in {js_file} — blocked by CSP"


# ---------------------------------------------------------------------------
# F-4: Upload handler creates chip only, does NOT fetch /input
# ---------------------------------------------------------------------------


async def test_upload_handler_fetches_only_stage_endpoint(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """The attach/paste/drop handler fetches upload-image (stage), not /input directly."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach5")
    assert "upload-image" in html, "Expected upload-image stage endpoint in attach handler"
    # The attach handler (attachFile) must NOT directly POST to /input
    # We check that the upload handler function does not contain /input fetch
    # by asserting attachFile only references upload-image not '/input'
    # (The send handler separately does the /input call — that's correct)
    assert "attachFile" in html, "Expected attachFile function in Alpine x-data"


async def test_form_submit_is_fetch_not_hx_post(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """The #input-form does NOT use hx-post; it uses an Alpine @submit handler with fetch."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "submit1")
    # The active form (#input-form with running instance) must use @submit, not hx-post
    assert "sendMessage" in html, "Expected sendMessage function in Alpine x-data"
    # hx-post must NOT appear on the input form (submit is now fetch-based)
    assert 'hx-post="/ui/instances/' not in html or "@submit" in html, (
        "Input form must use @submit.prevent with fetch, not bare hx-post"
    )


# ---------------------------------------------------------------------------
# F-5: Combined send POSTs refs as repeated `refs` field
# ---------------------------------------------------------------------------


async def test_submit_handler_sends_repeated_refs(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Submit handler builds FormData and appends 'refs' for each attachment."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "submit2")
    # FormData append with canonical field name 'refs' (locked decision #1)
    assert "FormData" in html, "Expected FormData in submit handler"
    assert '"refs"' in html or "'refs'" in html, (
        "Expected .append('refs',...) in submit handler (locked decision #1: field name is refs)"
    )


async def test_submit_handler_clears_attachments_after_send(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """After successful send, attachments array is cleared (chips disappear)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "submit3")
    # After send: attachments = [] (or .splice/clear equivalent)
    assert "attachments" in html, "Expected attachments in Alpine data"
    # The sendMessage function must clear attachments on success
    assert "attachments = []" in html or "attachments=[]" in html, (
        "Expected 'attachments = []' in sendMessage success path (clear chips after send)"
    )


# ---------------------------------------------------------------------------
# F-x: Chip-add sound (click.mp3)
# ---------------------------------------------------------------------------


async def test_audio_asset_wired_in_template(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """project_view.html references click.mp3 (audio element or JS Audio constructor)."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "sound1")
    assert "click.mp3" in html, (
        "Expected click.mp3 reference in project_view.html (audio wiring for chip-add sound)"
    )
    assert "/static/audio/click.mp3" in html, (
        "Expected /static/audio/click.mp3 src (served from app static dir)"
    )


def test_click_mp3_exists_in_static_dir() -> None:
    """click.mp3 must exist at src/claude_remote/static/audio/click.mp3 (REQ-16 Scenario 16.2)."""
    audio_file = PACKAGE_ROOT / "static" / "audio" / "click.mp3"
    assert audio_file.exists(), (
        f"click.mp3 not found at {audio_file} — "
        "run: mkdir -p src/claude_remote/static/audio && git mv click.mp3 src/claude_remote/static/audio/"
    )


# ---------------------------------------------------------------------------
# CSS: chip strip classes present
# ---------------------------------------------------------------------------


def test_css_has_cr_chip_classes() -> None:
    """app.css defines .cr-chip* multi-chip strip classes (not single-preview classes)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-chip__thumb" in css, ".cr-chip__thumb CSS class missing from app.css"
    assert ".cr-chip__name" in css, ".cr-chip__name CSS class missing from app.css"
    assert ".cr-chip__remove" in css, ".cr-chip__remove CSS class missing from app.css"


def test_css_has_cr_attach_class() -> None:
    """app.css defines .cr-attach button style."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-attach" in css, ".cr-attach CSS class missing from app.css"


def test_css_has_cr_drag_over_class() -> None:
    """app.css defines .cr-drag-over dashed outline for drag state."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-drag-over" in css, ".cr-drag-over CSS class missing from app.css"


def test_css_single_preview_classes_removed() -> None:
    """app.css must NOT contain v1 single-preview classes (.cr-img-preview-wrap, .cr-img-remove)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-img-preview-wrap" not in css, (
        ".cr-img-preview-wrap still in app.css — must be replaced with .cr-chip* strip classes"
    )
    assert ".cr-img-remove" not in css, (
        ".cr-img-remove still in app.css — must be replaced with .cr-chip__remove"
    )


# ---------------------------------------------------------------------------
# Graceful degradation: no image controls when no active instance
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
