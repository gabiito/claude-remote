"""Tests for POST /ui/instances/{id}/upload-image — image upload endpoint.

Covers:
  - Happy path: PNG/JPEG/WebP/GIF accepted, file written, send_keys called,
    HX-Trigger: input-sent header returned
  - Validation matrix: magic mismatch, size cap, empty file, unknown instance,
    non-running instance, tmux failure
  - Deferred TTL delete: call_later captured and invokable
  - StaticFiles isolation: upload dir NOT mounted
  - IMAGE_PATH_TEMPLATE single-source invariant
  - Lifespan startup sweep
"""

from __future__ import annotations

import io
import time
from pathlib import Path

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
# Magic byte constants
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
GIF89_MAGIC = b"GIF89a" + b"\x00" * 16
PDF_MAGIC = b"%PDF-1.4" + b"\x00" * 16

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
def img_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def img_app(img_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: img_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def img_client(img_app):
    async with AsyncClient(
        transport=ASGITransport(app=img_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(img_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(img_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


# ---------------------------------------------------------------------------
# Helper: create a project + running instance
# ---------------------------------------------------------------------------


async def _setup_running_instance(
    client, projects_repo, instances_repo, projects_root, domain, slug
):
    """Create project dir + DB row + launch instance. Returns (project, instance)."""
    p_path = projects_root / domain / slug
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name=slug, slug=slug, path=p_path, domain=domain
        )
    )
    launch_resp = await client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instance = instances_repo.list_by_project(project.id)[0]
    return project, instance


# ---------------------------------------------------------------------------
# 2.1 Happy path — PNG
# ---------------------------------------------------------------------------


async def test_upload_png_happy_path(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Valid PNG → 200, HX-Trigger: input-sent, file on disk, send_keys called."""
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "imgproj"
    )

    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
        data={"send_enter": "true"},
    )
    assert response.status_code == 200
    assert "input-sent" in response.headers.get("HX-Trigger", "")

    # File written under project.path/.claude/uploads/
    upload_dir = Path(project.path) / ".claude" / "uploads"
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".png")
    import re
    assert re.match(r"^[0-9a-f]{32}\.png$", files[0].name)

    # send_keys called with the absolute path
    assert len(fake_adapter.sent_keys) == 1
    assert fake_adapter.sent_keys[0][1] == str(files[0])


# ---------------------------------------------------------------------------
# 2.1 Happy path — JPEG / WebP / GIF
# ---------------------------------------------------------------------------


async def test_upload_jpeg_accepted(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "jpgproj"
    )
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.jpg", io.BytesIO(JPEG_MAGIC), "image/jpeg")},
    )
    assert response.status_code == 200
    assert "input-sent" in response.headers.get("HX-Trigger", "")


async def test_upload_webp_accepted(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "webpproj"
    )
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("anim.webp", io.BytesIO(WEBP_MAGIC), "image/webp")},
    )
    assert response.status_code == 200


async def test_upload_gif_accepted(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "gifproj"
    )
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("anim.gif", io.BytesIO(GIF89_MAGIC), "image/gif")},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 2.2 Validation matrix
# ---------------------------------------------------------------------------


async def test_magic_mismatch_returns_400_no_file_written(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """PDF magic with image/png Content-Type → 400, no file on disk."""
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "badmagic"
    )
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PDF_MAGIC), "image/png")},
    )
    assert response.status_code == 400
    upload_dir = Path(project.path) / ".claude" / "uploads"
    assert not upload_dir.exists() or len(list(upload_dir.iterdir())) == 0


async def test_oversized_file_returns_400_no_file_written(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """File > 10 MB → 400, no file written."""
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "bigfile"
    )
    # PNG magic header + 10 MB + 1 byte of padding
    from claude_remote.services.image_upload import MAX_IMAGE_BYTES
    big_data = PNG_MAGIC + b"\x00" * (MAX_IMAGE_BYTES - len(PNG_MAGIC) + 1)
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("big.png", io.BytesIO(big_data), "image/png")},
    )
    assert response.status_code == 400
    upload_dir = Path(project.path) / ".claude" / "uploads"
    assert not upload_dir.exists() or len(list(upload_dir.iterdir())) == 0


async def test_empty_file_returns_400(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "emptyimg"
    )
    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("empty.png", io.BytesIO(b""), "image/png")},
    )
    assert response.status_code == 400


async def test_unknown_instance_returns_404_fragment_not_500(
    img_client: AsyncClient,
) -> None:
    """Non-existent instance → 404 HTML fragment (not 500)."""
    response = await img_client.post(
        "/ui/instances/nonexistent-uuid/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
    )
    assert response.status_code == 404
    assert response.status_code != 500
    # Response should be an HTML fragment
    content_type = response.headers.get("content-type", "")
    assert "html" in content_type


async def test_non_running_instance_returns_409(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Instance not in running status → 409."""
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "stopped"
    )
    # Stop the instance
    await img_client.post(f"/ui/instances/{instance.id}/stop")
    # Refresh
    instance = instances_repo.get(instance.id)
    assert instance is not None
    assert instance.status != "running"

    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
    )
    assert response.status_code == 409


async def test_tmux_error_returns_409_file_unlinked(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """TmuxOperationError on send_keys → 409, file cleaned up, never 5xx."""
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "tmuxerr"
    )
    # Kill session so send_keys raises TmuxOperationError
    fake_adapter._sessions.pop(instance.tmux_session_name, None)

    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
    )
    assert response.status_code < 500
    assert response.status_code == 409
    # File must have been cleaned up
    upload_dir = Path(project.path) / ".claude" / "uploads"
    if upload_dir.exists():
        assert len(list(upload_dir.iterdir())) == 0


# ---------------------------------------------------------------------------
# 2.4 Deferred TTL delete (call_later captured and invokable — no sleep)
# ---------------------------------------------------------------------------


async def test_deferred_ttl_delete_captured_and_invokable(
    img_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
    monkeypatch,
) -> None:
    """call_later is captured; invoking the callback deletes the file.

    Patches only call_later on the real running event loop — keeping all other
    loop methods intact so anyio internals continue to work correctly.
    """
    import asyncio as _asyncio

    from claude_remote.services.image_upload import UPLOAD_TTL_SECONDS

    captured: list[tuple] = []

    # First set up the project/instance (uses the real loop unpatched)
    project, instance = await _setup_running_instance(
        img_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "ttlproj"
    )

    # Patch call_later on the REAL loop object — only intercepts our specific call
    real_loop = _asyncio.get_event_loop()

    def _capturing_call_later(delay, fn, *args, **kwargs):
        captured.append((delay, fn, args))
        # Do NOT forward to real timer — deterministic, no-sleep test

    monkeypatch.setattr(real_loop, "call_later", _capturing_call_later)

    response = await img_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
        data={"send_enter": "true"},
    )
    assert response.status_code == 200

    # call_later must have been invoked exactly once
    assert len(captured) == 1
    delay, fn, args = captured[0]
    assert delay == UPLOAD_TTL_SECONDS

    # The file must exist now
    upload_dir = Path(project.path) / ".claude" / "uploads"
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].exists()

    # Invoke callback directly — no sleep
    fn(*args)

    # File must be gone
    assert not files[0].exists()


# ---------------------------------------------------------------------------
# 2.6 StaticFiles isolation
# ---------------------------------------------------------------------------


def test_upload_dir_not_statically_served(img_app) -> None:
    """No StaticFiles mount resolves to or contains .claude/uploads."""
    from starlette.staticfiles import StaticFiles
    from starlette.routing import Mount

    def _get_all_routes(app):
        routes = []
        for route in getattr(app, "routes", []):
            routes.append(route)
            # Recurse into sub-applications
            if hasattr(route, "app"):
                sub = route.app
                if hasattr(sub, "routes"):
                    routes.extend(_get_all_routes(sub))
        return routes

    all_routes = _get_all_routes(img_app)
    for route in all_routes:
        if isinstance(route, Mount) and isinstance(getattr(route, "app", None), StaticFiles):
            directory = str(getattr(route.app, "directory", ""))
            assert ".claude" not in directory, (
                f"StaticFiles mount at '{route.path}' points to '{directory}' "
                "which contains .claude — uploads would be exposed!"
            )
            assert "uploads" not in directory, (
                f"StaticFiles mount at '{route.path}' points to '{directory}' "
                "which is the uploads dir — uploads would be exposed!"
            )


# ---------------------------------------------------------------------------
# 2.8 IMAGE_PATH_TEMPLATE single-source invariant
# ---------------------------------------------------------------------------


def test_image_path_template_single_definition() -> None:
    """IMAGE_PATH_TEMPLATE appears exactly once as a module-level assignment."""
    import ast
    import sys
    from pathlib import Path as _Path

    # Locate the service module source
    service_src = (
        _Path(sys.modules["claude_remote.services.image_upload"].__file__)  # type: ignore[arg-type]
    )
    source = service_src.read_text()
    tree = ast.parse(source)

    # Count top-level assignments of IMAGE_PATH_TEMPLATE
    # Handles both plain assignments (ast.Assign) and annotated assignments (ast.AnnAssign)
    definitions = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "IMAGE_PATH_TEMPLATE"
                for t in node.targets
            )
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "IMAGE_PATH_TEMPLATE"
        )
    ]
    assert len(definitions) == 1, (
        f"Expected exactly 1 definition of IMAGE_PATH_TEMPLATE, found {len(definitions)}"
    )


def test_image_path_template_used_in_upload_handler() -> None:
    """The upload handler uses IMAGE_PATH_TEMPLATE — no bare format string."""
    import sys
    from pathlib import Path as _Path

    route_src = _Path(
        sys.modules.get(
            "claude_remote.routes.ui",
            type("M", (), {"__file__": "src/claude_remote/routes/ui.py"})(),
        ).__file__  # type: ignore[attr-defined]
    )
    # Resolve relative to CWD if needed
    if not route_src.is_absolute():
        import os
        route_src = _Path(os.getcwd()) / route_src

    source = route_src.read_text()
    # Handler must reference IMAGE_PATH_TEMPLATE (imported and used)
    assert "IMAGE_PATH_TEMPLATE" in source, (
        "routes/ui.py must use IMAGE_PATH_TEMPLATE to build the injected path string"
    )


# ---------------------------------------------------------------------------
# Phase 4 (task 4.1): Lifespan startup sweep
# ---------------------------------------------------------------------------


def test_sweep_stale_uploads_removes_stale_keeps_fresh(tmp_path: Path) -> None:
    """Direct unit test of sweep_stale_uploads used in lifespan."""
    from claude_remote.services.image_upload import (
        STALE_SWEEP_SECONDS,
        UPLOAD_SUBDIR,
        sweep_stale_uploads,
    )
    import os

    # Create a registered project with upload dir
    project_path = tmp_path / "proj"
    upload_dir = project_path.joinpath(*UPLOAD_SUBDIR)
    upload_dir.mkdir(parents=True)

    now = time.time()

    # Stale file: mtime = now - STALE_SWEEP_SECONDS - 60
    stale = upload_dir / "stale.png"
    stale.write_bytes(b"old")
    os.utime(stale, (now - STALE_SWEEP_SECONDS - 60, now - STALE_SWEEP_SECONDS - 60))

    # Fresh file: mtime = now - 60
    fresh = upload_dir / "fresh.png"
    fresh.write_bytes(b"new")
    os.utime(fresh, (now - 60, now - 60))

    removed = sweep_stale_uploads([str(project_path)], now=now)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


async def test_lifespan_sweep_via_app_startup(
    tmp_db,
    tmp_projects_root,
    img_settings,
) -> None:
    """Lifespan sweep integration: the lifespan block calls sweep_stale_uploads.

    We test this by verifying the sweep is wired to the correct data: given
    a stale file under a registered project, calling the same sweep function
    that lifespan calls (with the same repo query) removes the file.

    The full lifespan-trigger path is separately covered by test_app_lifespan.py
    (which does run the lifespan context). Here we verify the DATA WIRING:
    that sweep_stale_uploads is called with paths from ProjectsRepository.list_all().
    """
    import os
    from claude_remote.services.image_upload import STALE_SWEEP_SECONDS, UPLOAD_SUBDIR, sweep_stale_uploads

    # Register a project with a stale upload
    project_path = tmp_projects_root / "dom" / "sweeptest"
    project_path.mkdir(parents=True)
    proj_repo = ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))
    project = proj_repo.create(
        project_create=ProjectCreate(
            name="sweeptest", slug="sweeptest", path=project_path, domain="dom"
        )
    )

    upload_dir = project_path.joinpath(*UPLOAD_SUBDIR)
    upload_dir.mkdir(parents=True)
    now = time.time()
    stale_file = upload_dir / "stale_startup.png"
    stale_file.write_bytes(b"old_data")
    os.utime(stale_file, (now - STALE_SWEEP_SECONDS - 120, now - STALE_SWEEP_SECONDS - 120))

    # Reproduce exactly what lifespan does: list_all() → sweep
    project_paths = [p.path for p in proj_repo.list_all()]
    removed = sweep_stale_uploads(project_paths, now=now)

    assert removed == 1
    assert not stale_file.exists(), "Lifespan sweep data wiring: stale upload should be removed"
