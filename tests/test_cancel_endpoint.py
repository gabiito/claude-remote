"""Tests for DELETE /ui/instances/{id}/upload-image/{ref} — cancel endpoint (B-3 RED).

All tests must FAIL until the cancel endpoint is implemented (B-4 GREEN).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.services.image_upload import UPLOAD_SUBDIR
from claude_remote.services.tmux_adapter import FakeTmuxAdapter

pytestmark = pytest.mark.anyio

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


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
def cancel_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def cancel_app(cancel_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: cancel_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def cancel_client(cancel_app):
    async with AsyncClient(
        transport=ASGITransport(app=cancel_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(cancel_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(cancel_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


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


def _stage_file(project: object, filename: str | None = None) -> Path:
    """Write a fake staged PNG file directly to the uploads dir."""
    uploads = Path(project.path).joinpath(*UPLOAD_SUBDIR)  # type: ignore[attr-defined]
    uploads.mkdir(parents=True, exist_ok=True)
    name = filename or f"{uuid.uuid4().hex}.png"
    p = uploads / name
    p.write_bytes(PNG_MAGIC)
    return p


# ---------------------------------------------------------------------------
# B-3 Tests
# ---------------------------------------------------------------------------


async def test_cancel_valid_ref_deletes_file(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """Stage a file, then DELETE /upload-image/{ref} → file gone, status 204."""
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "cancel-ok"
    )
    staged = _stage_file(project)

    response = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{staged.name}"
    )
    assert response.status_code == 204, (
        f"Expected 204, got {response.status_code}: {response.text}"
    )
    assert not staged.exists(), "Staged file must be deleted after cancel"


async def test_cancel_unknown_ref_returns_204(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """DELETE with a valid-format ref that was never staged → 204 (idempotent).

    Per locked decision #3: format-valid + containment-valid refs return 204
    regardless of file existence.  A never-staged UUID is indistinguishable from
    a previously-staged-then-deleted UUID — both are valid refs pointing to an
    absent file inside THIS instance's uploads dir.
    """
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "cancel-404"
    )
    unknown_ref = f"{uuid.uuid4().hex}.png"

    response = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{unknown_ref}"
    )
    assert response.status_code == 204, (
        f"Valid-format ref (no file) must return 204 per locked decision #3, "
        f"got {response.status_code}"
    )


async def test_cancel_foreign_instance_ref_does_not_delete_foreign_file(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """File under instance B's project, cancel against instance A → B's file untouched.

    The critical security invariant: cancel against instance A MUST NOT delete a
    file that lives under instance B's uploads dir.  Per locked decision #3 the
    cancel returns 204 (format-valid ref, file just absent in A's dir), but B's
    file must still exist after the request.
    """
    project_a, instance_a = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "cancel-a"
    )
    project_b, instance_b = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "cancel-b"
    )
    staged_b = _stage_file(project_b)

    response = await cancel_client.delete(
        f"/ui/instances/{instance_a.id}/upload-image/{staged_b.name}"
    )
    # 2xx expected (format valid, containment within A's dir passes, file absent in A)
    assert response.status_code < 500, (
        f"Cancel must never 5xx, got {response.status_code}"
    )
    # THE CRITICAL INVARIANT: B's file must not be touched
    assert staged_b.exists(), (
        "B's file MUST NOT be deleted when cancel targets A — "
        "cross-instance deletion is the security boundary"
    )


async def test_cancel_traversal_ref_rejected(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """ref '../../../../etc/passwd' → 404, no FS outside uploads touched."""
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root,
        "acme.com", "cancel-traversal"
    )
    # URL-encode the traversal ref so FastAPI doesn't reject it as a path separator
    import urllib.parse
    traversal_ref = urllib.parse.quote("../../../../etc/passwd", safe="")

    response = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{traversal_ref}"
    )
    # Must not succeed; traversal should be rejected (404 or 400 — not 2xx, not 5xx)
    assert response.status_code in (400, 404), (
        f"Traversal ref must be rejected, got {response.status_code}"
    )
    assert response.status_code < 500


async def test_cancel_already_deleted_is_204_not_5xx(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """Delete file manually, then cancel → 204 (idempotent), never 404 or 5xx.

    Per locked decision #3: a cancel for a ref whose FORMAT is valid AND that
    resolves (by containment rules) to within THIS instance's uploads dir MUST
    return 204 whether or not the file currently exists.  Returning 404 for a
    "valid ref, file already gone" case breaks idempotency (mobile double-tap /
    retry on chip-cancel → spurious 404).
    """
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "cancel-idem"
    )
    staged = _stage_file(project)
    ref = staged.name

    # Delete the file before the cancel request arrives
    staged.unlink()
    assert not staged.exists()

    response = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{ref}"
    )
    # Strictly 204 — not 404 — per locked decision #3 (idempotent best-effort)
    assert response.status_code == 204, (
        f"Idempotent cancel must return 204 (not 404) when file already gone, "
        f"got {response.status_code}"
    )


async def test_cancel_double_cancel_both_204(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """Cancel the same ref twice — both requests must return 204 (idempotency sentinel).

    First cancel: file exists, deleted, 204.
    Second cancel: file already gone, 204 (not 404).
    """
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root,
        "acme.com", "cancel-double"
    )
    staged = _stage_file(project)
    ref = staged.name

    # First cancel — file exists
    resp1 = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{ref}"
    )
    assert resp1.status_code == 204, (
        f"First cancel must return 204, got {resp1.status_code}"
    )
    assert not staged.exists(), "File must be deleted after first cancel"

    # Second cancel — file already gone
    resp2 = await cancel_client.delete(
        f"/ui/instances/{instance.id}/upload-image/{ref}"
    )
    assert resp2.status_code == 204, (
        f"Second (idempotent) cancel must return 204, got {resp2.status_code}: "
        "double-tap / retry must not return 404"
    )


async def test_cancel_never_5xx(
    cancel_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
) -> None:
    """Cancel must never return 5xx regardless of error condition."""
    project, instance = await _setup_running_instance(
        cancel_client, projects_repo, instances_repo, tmp_projects_root,
        "acme.com", "cancel-never5xx"
    )

    error_cases = [
        f"{uuid.uuid4().hex}.png",   # valid uuid format, file missing
        "nonexistent.xyz",            # unknown extension ref
        f"a{'b' * 63}.png",           # very long ref
    ]
    for ref in error_cases:
        response = await cancel_client.delete(
            f"/ui/instances/{instance.id}/upload-image/{ref}"
        )
        assert response.status_code < 500, (
            f"Cancel returned {response.status_code} for ref={ref!r} — must never be 5xx"
        )
