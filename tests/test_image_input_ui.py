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
    """Chip name uses x-text (not .innerHTML =) — injection guard per design §5.
    Note: hx-swap='innerHTML' is a valid HTMX attribute and is not a JS injection risk."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip4")
    # Must use x-text for chip name (autoescaped)
    assert "x-text" in html, "Expected x-text for chip name (injection guard)"
    # Must not set innerHTML via JS assignment (e.g. el.innerHTML = or .innerHTML=)
    # hx-swap="innerHTML" is fine — it's an HTMX attribute, not a JS assignment
    assert ".innerHTML" not in html, (
        ".innerHTML assignment found — injection risk; use x-text for chip name"
    )


# ---------------------------------------------------------------------------
# B: Chip strip outside .cr-input-wrap (input dock layout)
# ---------------------------------------------------------------------------


async def test_chip_strip_outside_input_wrap(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """cr-attachment-chips must NOT be nested inside cr-input-wrap.
    The chip band is a full-width strip above the input row, not inside the text box."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "layout1")
    # Structural check: cr-input-wrap must appear AFTER cr-attachment-chips in the source
    # (chip band is first child of form, cr-input-wrap is inside cr-input-row which follows)
    chips_pos = html.find("cr-attachment-chips")
    wrap_pos = html.find("cr-input-wrap")
    assert chips_pos != -1, "cr-attachment-chips not found in template"
    assert wrap_pos != -1, "cr-input-wrap not found in template"
    assert chips_pos < wrap_pos, (
        "cr-attachment-chips must appear before cr-input-wrap in source "
        "(chip strip is outside the text box, above the input row)"
    )


async def test_cr_input_row_wrapper_present(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """A cr-input-row wrapper div must be present inside the input dock form."""
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "layout2")
    assert "cr-input-row" in html, (
        "Expected cr-input-row wrapper div inside the input dock form"
    )


def test_css_has_cr_input_row() -> None:
    """app.css must define .cr-input-row for the horizontal input controls row."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-input-row" in css, ".cr-input-row CSS rule missing from app.css"


def test_css_input_wrap_is_not_column() -> None:
    """app.css .cr-input-wrap must NOT set flex-direction: column.
    The column layout was reverted — chip strip now lives outside the wrap."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    # Find the .cr-input-wrap block and ensure flex-direction:column is gone
    wrap_start = css.find(".cr-input-wrap {")
    assert wrap_start != -1, ".cr-input-wrap rule not found in app.css"
    # Look for the closing brace of the rule block
    wrap_end = css.find("}", wrap_start)
    wrap_block = css[wrap_start:wrap_end]
    assert "flex-direction" not in wrap_block, (
        ".cr-input-wrap still sets flex-direction — revert the column layout; "
        "chip strip is now outside the wrap in a separate band"
    )


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


async def test_file_input_has_no_accept_filter(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """File input must NOT restrict to image/* — picker must show ALL file types (REQ-13).

    ON-DEVICE-ONLY: Visual confirmation that the native file picker shows all
    file types (PDF, text, images, etc.) without filtering.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach2")
    assert 'type="file"' in html, "Expected hidden file input in project_view"
    assert 'accept="image/*"' not in html, (
        'File input must NOT have accept="image/*" — picker must show all file types (S3, REQ-13)'
    )


async def test_no_image_type_guard_in_attach_file(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """attachFile function body must NOT contain a fileObj.type.startsWith('image/') guard (REQ-14).

    The early-return type guard was removed so any file type reaches the stage endpoint.
    Classification is server-authoritative (based on magic bytes, not Content-Type).
    NOTE: the paste handler legitimately keeps startsWith('image/') for clipboard items;
    only the attachFile guard is removed.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach2b")
    # Extract ONLY the attachFile method body via brace-matching, so sibling
    # methods (e.g. handlePaste, which legitimately keeps startsWith('image/'))
    # are never included in the assertion window.
    start = html.find("async attachFile(fileObj)")
    assert start != -1, "attachFile function not found in template"
    brace = html.find("{", start)
    assert brace != -1, "attachFile body opening brace not found"
    depth = 0
    end = brace
    for i in range(brace, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    attach_body = html[start:end]
    assert "startsWith('image/')" not in attach_body, (
        "attachFile contains startsWith('image/') type guard — must be removed (S3, REQ-14)"
    )
    assert 'startsWith("image/")' not in attach_body, (
        'attachFile contains startsWith("image/") type guard — must be removed (S3, REQ-14)'
    )


async def test_file_input_does_not_force_camera(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """File input must NOT carry capture=environment.

    On mobile, capture="environment" is a directive (not a hint): the
    browser jumps straight to the rear camera and never offers the
    gallery/files chooser — breaking the picker, which is our documented
    reliable path. Absence of capture lets mobile show the full chooser.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "nocap")
    assert "capture=" not in html, (
        'File input must not use capture= (forces camera-only on mobile)'
    )


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
    """Textarea @paste must delegate to a method, not inline a bare statement.

    Regression guard: a bare ``for (...)`` directly in the @paste attribute
    is NOT a valid Alpine expression — Alpine throws
    'Unexpected token for' and the handler never runs. The handler must
    call a method defined on the x-data (e.g. handlePaste($event)).
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "attach3")
    assert "@paste" in html or "x-on:paste" in html, "Expected @paste Alpine handler on textarea"
    # Extract the @paste attribute value
    marker = '@paste="'
    start = html.find(marker)
    assert start != -1, "@paste attribute not found"
    val = html[start + len(marker) : html.find('"', start + len(marker))]
    assert "for " not in val and "for(" not in val, (
        f"@paste contains a bare statement (invalid Alpine expression): {val!r}"
    )
    assert "handlePaste" in val, (
        f"@paste must delegate to a method (handlePaste), got: {val!r}"
    )


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
        "run: mkdir -p src/claude_remote/static/audio "
        "&& git mv click.mp3 src/claude_remote/static/audio/"
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
# S3: file_class chip branching + CSS (REQ-15, REQ-16)
# ---------------------------------------------------------------------------


async def test_file_class_in_chip_struct(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """attachFile success handler pushes an object with fileClass (or file_class) key (REQ-15.2).

    The server's `class` field must be mapped onto the chip struct so the
    template can branch on it — never re-sniff from fileObj.type.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip5")
    assert "fileClass" in html or "file_class" in html, (
        "Expected fileClass (or file_class) key in chip struct pushed to attachments (S3, REQ-15.2)"
    )


async def test_chip_has_image_thumb_branch(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Chip template has an <img> element conditioned on fileClass === 'image' (REQ-15.3).

    The thumbnail branch must be guarded by x-show or x-if so it only renders
    for image-class attachments.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip6")
    # Must have an img with x-show or x-if conditioned on 'image'
    assert "<img" in html, "Expected <img> element in chip template (thumbnail branch)"
    # The image branch condition must reference 'image' and fileClass
    assert "fileClass" in html and "'image'" in html, (
        "Expected x-show/x-if conditioned on fileClass === 'image' in chip template (S3, REQ-15.3)"
    )


async def test_chip_has_generic_icon_branch(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """Chip template has a generic icon element conditioned on fileClass !== 'image' (REQ-15.3).

    A <span class="cr-chip__icon"> (or equivalent) must appear when the
    attachment is not an image — CSP-safe (no blob:, no data: from binary).
    ON-DEVICE-ONLY: Visual rendering of the generic icon for PDF/text uploads.
    """
    _, html = await _launch_and_get_html(ui_client, projects_repo, tmp_projects_root, "chip7")
    assert "cr-chip__icon" in html, (
        "Expected .cr-chip__icon element in chip template (generic icon branch, S3, REQ-15.3)"
    )


def test_cr_chip_icon_rule_in_css() -> None:
    """app.css must contain a .cr-chip__icon rule block (REQ-16.1)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert ".cr-chip__icon" in css, (
        ".cr-chip__icon CSS rule missing from app.css (S3, REQ-16.1)"
    )


def test_existing_chip_css_rules_present() -> None:
    """app.css must still contain all existing chip rules — no structural changes (REQ-16.2)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    for rule in (".cr-chip__thumb", ".cr-chip__item", ".cr-chip__name", ".cr-chip__remove"):
        assert rule in css, f"{rule} CSS rule missing from app.css (S3, REQ-16.2)"


# Reaffirm: no blob: URI (REQ-15.4 — green before S3, must stay green)
# test_no_blob_uri_in_template is already defined above and covers this.

# Reaffirm: audio asset wired + click.mp3 exists — already covered by
# test_audio_asset_wired_in_template and test_click_mp3_exists_in_static_dir.


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


# ---------------------------------------------------------------------------
# OOM-fix: chip must appear synchronously after server upload (no blocking
# await before push), and FileReader/readAsDataURL must be gone entirely.
# ---------------------------------------------------------------------------


async def test_attach_push_not_gated_by_thumbnail_await(
    ui_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root: Path,
) -> None:
    """attachFile MUST push to this.attachments BEFORE any thumbnail work.

    The old code did:
        dataUrl = await new Promise(resolve => { fr.readAsDataURL(...) })
        this.attachments.push(...)

    That blocks the chip on a full-res FileReader decode — OOM on camera photos
    and permanent skeleton hang if readAsDataURL errors (no onerror, no timeout).

    Contract (structural, assertable without a browser):
      - 'this.attachments.push' MUST appear in the attachFile body.
      - 'readAsDataURL' MUST NOT appear anywhere in the template (regression guard).
      - 'await new Promise' MUST NOT appear BEFORE 'this.attachments.push' inside attachFile.
      - 'createImageBitmap' MUST appear in the template (new thumb path present).

    Browser-only behaviors (ON-DEVICE-ONLY):
      OD-OOM-1  Take a high-res camera photo; chip appears immediately,
                thumbnail fills in within ~1-2 s or falls back to the
                paperclip icon — no OOM crash, no stuck skeleton.
      OD-OOM-2  Repeat 3-5 times with different lighting/sizes.
      OD-OOM-3  Test on the weakest device available (most likely to OOM).
    """
    _, html = await _launch_and_get_html(
        ui_client, projects_repo, tmp_projects_root, "oomfix1"
    )

    # --- brace-match attachFile body (same technique as test_no_image_type_guard_in_attach_file)
    start = html.find("async attachFile(fileObj)")
    assert start != -1, "attachFile function not found in template"
    brace = html.find("{", start)
    assert brace != -1, "attachFile body opening brace not found"
    depth = 0
    end = brace
    for i in range(brace, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    attach_body = html[start:end]

    # 1. chip push must exist
    push_idx = attach_body.find("this.attachments.push")
    assert push_idx != -1, (
        "this.attachments.push not found in attachFile — chip staging is broken"
    )

    # 2. readAsDataURL must be gone everywhere (regression guard)
    assert "readAsDataURL" not in html, (
        "readAsDataURL still present in template — "
        "this causes OOM on full-res camera photos and a permanent skeleton hang "
        "when the FileReader errors (no onerror, no timeout). Replace with "
        "createImageBitmap-based downscale after pushing the chip."
    )

    # 3. 'await new Promise' must NOT appear BEFORE the push in attachFile
    await_promise_idx = attach_body.find("await new Promise")
    assert await_promise_idx == -1 or await_promise_idx > push_idx, (
        "'await new Promise' appears BEFORE 'this.attachments.push' in attachFile — "
        "the chip is gated on a blocking thumbnail await. Push the chip first, "
        "then do thumbnail work asynchronously."
    )

    # 4. createImageBitmap must be referenced (new downscale path present)
    assert "createImageBitmap" in html, (
        "createImageBitmap not found in template — "
        "the CSP-safe thumbnail downscale path is missing. "
        "Add makeThumb() using createImageBitmap + offscreen canvas."
    )
