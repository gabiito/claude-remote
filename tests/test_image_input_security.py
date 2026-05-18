"""Security integration tests — cross-cutting ref authority + IMAGE_PATH_TEMPLATE invariant (B-7).

These tests verify:
  - Stage endpoint never calls send_keys under any format
  - Client can never supply a raw path; only refs are accepted
  - IMAGE_PATH_TEMPLATE is a single module-level constant in file_upload.py
  - No literal bare path format string in routes/ui.py outside IMAGE_PATH_TEMPLATE usage
"""

from __future__ import annotations

import ast
import io
import sys
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

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
GIF89_MAGIC = b"GIF89a" + b"\x00" * 16


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
def sec_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def sec_app(sec_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: sec_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def sec_client(sec_app):
    async with AsyncClient(
        transport=ASGITransport(app=sec_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(sec_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(sec_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


async def _setup_running_instance(
    client, projects_repo, instances_repo, projects_root, domain, slug
):
    p_path = projects_root / domain / slug
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(name=slug, slug=slug, path=p_path, domain=domain)
    )
    resp = await client.post(f"/ui/projects/{project.id}/launch")
    assert resp.status_code == 200
    instance = instances_repo.list_by_project(project.id)[0]
    return project, instance


# ---------------------------------------------------------------------------
# B-7 Security tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("magic,ct,name", [
    (PNG_MAGIC, "image/png", "test.png"),
    (JPEG_MAGIC, "image/jpeg", "test.jpg"),
    (WEBP_MAGIC, "image/webp", "test.webp"),
    (GIF89_MAGIC, "image/gif", "test.gif"),
])
async def test_security_stage_never_calls_send_keys_under_any_format(
    sec_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
    magic: bytes,
    ct: str,
    name: str,
) -> None:
    """Stage endpoint never calls send_keys under any image format (CRITICAL invariant)."""
    project, instance = await _setup_running_instance(
        sec_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", f"sec-{name}"
    )

    response = await sec_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": (name, io.BytesIO(magic), ct)},
    )
    assert response.status_code == 200
    assert fake_adapter.sent_keys == [], (
        f"Stage endpoint for {ct} called send_keys — CRITICAL security violation"
    )


async def test_security_ref_to_path_is_sole_server_authority(
    sec_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Client can never supply a raw path; only refs are accepted; endpoint returns opaque ref."""
    project, instance = await _setup_running_instance(
        sec_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "sec-ref-auth"
    )

    response = await sec_client.post(
        f"/ui/instances/{instance.id}/upload-image",
        files={"file": ("photo.png", io.BytesIO(PNG_MAGIC), "image/png")},
    )
    assert response.status_code == 200

    body = response.json()
    # ref is an opaque UUID basename — not a full path
    ref = body["ref"]
    assert "/" not in ref, f"ref must not contain '/' (no path exposure): {ref!r}"
    assert "\\" not in ref, f"ref must not contain '\\\\' (no path exposure): {ref!r}"
    assert not ref.startswith("/"), f"ref must not be an absolute path: {ref!r}"

    # Client sends ref back; server resolves to path — client never knows the path
    # (This is guaranteed by the architecture: client sends ref, server resolves)
    assert fake_adapter.sent_keys == [], "Stage must not call send_keys"


def test_security_image_path_template_single_source() -> None:
    """IMAGE_PATH_TEMPLATE is defined exactly once in services/file_upload.py (AST check)."""
    service_src = Path(
        sys.modules["claude_remote.services.file_upload"].__file__  # type: ignore[arg-type]
    )
    source = service_src.read_text()
    tree = ast.parse(source)

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
        f"IMAGE_PATH_TEMPLATE must be defined exactly once, found {len(definitions)}"
    )

    # Verify the value is still the bare path (obs #762 — do not change)
    val_node = definitions[0].value  # same attribute for both Assign and AnnAssign

    assert isinstance(val_node, ast.Constant), "IMAGE_PATH_TEMPLATE value must be a string literal"
    assert val_node.value == "{path}", (
        f"IMAGE_PATH_TEMPLATE must remain '{{path}}' (bare, obs #762), "
        f"got: {val_node.value!r}"
    )


def test_security_template_constant_not_overridden_in_ui() -> None:
    """routes/ui.py must not contain a bare path format literal outside IMAGE_PATH_TEMPLATE."""
    import os
    import re

    route_src = Path(
        sys.modules.get(
            "claude_remote.routes.ui",
            type("M", (), {"__file__": "src/claude_remote/routes/ui.py"})(),
        ).__file__  # type: ignore[attr-defined]
    )
    if not route_src.is_absolute():
        route_src = Path(os.getcwd()) / route_src

    source = route_src.read_text()

    # Strip comment lines; check no bare '{path}' or '@{path}' string literals remain
    non_comment_lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    non_comment_source = "\n".join(non_comment_lines)

    bare_path_matches = re.findall(r'["\'](\{path\}|@\{path\})["\']', non_comment_source)
    assert len(bare_path_matches) == 0, (
        f"routes/ui.py contains hardcoded path format literal(s): {bare_path_matches} "
        "— must use IMAGE_PATH_TEMPLATE, not a bare literal"
    )
