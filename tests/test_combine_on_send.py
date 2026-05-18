"""Tests for POST /ui/instances/{id}/input — combine-on-send extension (B-5 RED).

All tests must FAIL until post_instance_input is extended with refs support (B-6 GREEN).
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
def comb_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def comb_app(comb_settings, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: comb_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def comb_client(comb_app):
    async with AsyncClient(
        transport=ASGITransport(app=comb_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(comb_settings, tmp_db):
    return ProjectsRepository(connection_factory=lambda: get_connection_for(tmp_db))


@pytest.fixture()
def instances_repo(comb_settings, tmp_db):
    return InstancesRepository(connection_factory=lambda: get_connection_for(tmp_db))


# ---------------------------------------------------------------------------
# Helpers
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


def _stage_file(project: object, ext: str = ".png") -> Path:
    """Write a fake staged file to the uploads dir. Returns the Path."""
    uploads = Path(project.path).joinpath(*UPLOAD_SUBDIR)  # type: ignore[attr-defined]
    uploads.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    p = uploads / name
    p.write_bytes(PNG_MAGIC)
    return p


# ---------------------------------------------------------------------------
# B-5 Tests
# ---------------------------------------------------------------------------


async def test_combine_single_attachment_and_text(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """One staged file + text → exactly ONE sent_keys entry, payload = path + newline + text."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-single"
    )
    staged = _stage_file(project)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "describe this", "refs": staged.name},
    )
    assert response.status_code == 200

    assert len(fake_adapter.sent_keys) == 1, (
        f"Expected exactly 1 send_keys call, got {len(fake_adapter.sent_keys)}"
    )
    payload = fake_adapter.sent_keys[0][1]
    assert payload == f"{staged}\ndescribe this", (
        f"Payload must be '<path>\\n<text>', got: {payload!r}"
    )
    assert fake_adapter.sent_keys[0][2] is True, "send_enter must be True"


async def test_combine_multiple_attachments_and_text(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Two staged files + text → one sent_keys call, paths first then text."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-multi"
    )
    staged_a = _stage_file(project, ".png")
    staged_b = _stage_file(project, ".png")

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "what are these", "refs": [staged_a.name, staged_b.name]},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1
    payload = fake_adapter.sent_keys[0][1]
    assert payload == f"{staged_a}\n{staged_b}\nwhat are these", (
        f"Expected paths-first payload, got: {payload!r}"
    )
    assert fake_adapter.sent_keys[0][2] is True


async def test_combine_attachments_only_empty_text(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """One staged file + empty text → one sent_keys call, payload = path only."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-notext"
    )
    staged = _stage_file(project)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "", "refs": staged.name},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1
    payload = fake_adapter.sent_keys[0][1]
    assert payload == str(staged), (
        f"Empty text + one ref → payload is just the path, got: {payload!r}"
    )
    assert fake_adapter.sent_keys[0][2] is True


async def test_combine_no_attachments_text_only_unchanged(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """No refs field + text → behaviour identical to pre-amendment (regression guard)."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-textonly"
    )

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "hello"},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1
    assert fake_adapter.sent_keys[0][1] == "hello", (
        "No refs → payload must be text only (regression guard)"
    )


async def test_combine_empty_text_no_refs_still_400(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """No refs + empty text → 400 (existing guard unchanged per locked decision #4)."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-empty"
    )

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": ""},
    )
    assert response.status_code == 400, (
        f"Empty text + no refs must still return 400, got {response.status_code}"
    )
    assert fake_adapter.sent_keys == []


async def test_combine_traversal_ref_silently_dropped(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Traversal ref passed as refs → silently dropped, text still sent if provided."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-trav"
    )

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "hello", "refs": "../../../../etc/passwd"},
    )
    # Should succeed (text is present) — bad ref silently dropped
    assert response.status_code == 200
    # sent_keys should have been called with just "hello" (traversal ref dropped)
    assert len(fake_adapter.sent_keys) == 1
    payload = fake_adapter.sent_keys[0][1]
    assert "/etc/passwd" not in payload, (
        f"Traversal path must NOT appear in send_keys payload, got: {payload!r}"
    )
    assert payload == "hello"


async def test_combine_cross_instance_ref_rejected(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Ref valid under instance B sent to instance A → dropped, B's path not injected."""
    project_a, instance_a = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-xa"
    )
    project_b, instance_b = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-xb"
    )
    staged_b = _stage_file(project_b)

    response = await comb_client.post(
        f"/ui/instances/{instance_a.id}/input",
        data={"text": "hello", "refs": staged_b.name},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1
    payload = fake_adapter.sent_keys[0][1]
    # B's path must NOT appear in the payload sent via instance A
    assert str(staged_b) not in payload, (
        f"Cross-instance path must NOT appear in payload, got: {payload!r}"
    )
    assert payload == "hello"


async def test_combine_absolute_path_ref_rejected(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Absolute path ref ('/etc/shadow') → silently dropped, never injected."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-abspath"
    )

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "hello", "refs": "/etc/shadow"},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1
    payload = fake_adapter.sent_keys[0][1]
    assert "/etc/shadow" not in payload, (
        f"Absolute path ref must NOT appear in payload, got: {payload!r}"
    )
    assert payload == "hello"


async def test_combine_deferred_cleanup_scheduled_per_file_not_at_stage(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
    monkeypatch,
) -> None:
    """After combined send: call_later called once per resolved file (60s); invoke → file gone."""
    import asyncio as _asyncio

    from claude_remote.services.image_upload import UPLOAD_TTL_SECONDS

    captured: list[tuple] = []

    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-cleanup"
    )
    staged_a = _stage_file(project)
    staged_b = _stage_file(project)

    real_loop = _asyncio.get_event_loop()

    def _capture_call_later(delay, fn, *args, **kwargs):
        captured.append((delay, fn, args))

    monkeypatch.setattr(real_loop, "call_later", _capture_call_later)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "check these", "refs": [staged_a.name, staged_b.name]},
    )
    assert response.status_code == 200

    # call_later must be called once per resolved file
    assert len(captured) == 2, (
        f"Expected 2 call_later calls (one per file), got {len(captured)}"
    )
    for entry in captured:
        assert entry[0] == UPLOAD_TTL_SECONDS, (
            f"Expected delay={UPLOAD_TTL_SECONDS}, got {entry[0]}"
        )

    # Files must still exist before callbacks are invoked
    assert staged_a.exists()
    assert staged_b.exists()

    # Invoke callbacks directly — files must be gone
    for entry in captured:
        entry[1](*entry[2])

    assert not staged_a.exists()
    assert not staged_b.exists()


async def test_combine_staged_files_exist_before_send(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """At the moment send_keys is called, staged files still exist (deferred, not pre-send)."""
    files_existed_at_send: list[bool] = []
    original_send_keys = fake_adapter.send_keys

    def _capturing_send_keys(session, text, *, send_enter=True):
        # Check if staged files still exist at send time
        for path_str in text.split("\n"):
            p = Path(path_str)
            if p.suffix in (".png", ".jpg", ".webp", ".gif"):
                files_existed_at_send.append(p.exists())
        return original_send_keys(session, text, send_enter=send_enter)

    fake_adapter.send_keys = _capturing_send_keys  # type: ignore[method-assign]

    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-preexist"
    )
    staged = _stage_file(project)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "describe", "refs": staged.name},
    )
    assert response.status_code == 200
    assert all(files_existed_at_send), (
        "Staged files must exist at the time send_keys is called (delete is deferred, not pre-send)"
    )


async def test_combine_tmux_error_returns_4xx(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """FakeTmuxAdapter raises TmuxOperationError → 4xx, HTML fragment (REQ-10 Scenario 10.1)."""
    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-tmuxerr"
    )
    staged = _stage_file(project)

    # Kill session so send_keys raises TmuxOperationError
    fake_adapter._sessions.pop(instance.tmux_session_name, None)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "describe", "refs": staged.name},
    )
    assert 400 <= response.status_code < 500, (
        f"TmuxOperationError must return 4xx, got {response.status_code}"
    )
    content_type = response.headers.get("content-type", "")
    assert "html" in content_type


async def test_combine_image_path_template_used(
    comb_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root: Path,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """Resolved path is formatted via IMAGE_PATH_TEMPLATE.format(path=...) — single point."""
    from claude_remote.services.image_upload import IMAGE_PATH_TEMPLATE

    project, instance = await _setup_running_instance(
        comb_client, projects_repo, instances_repo, tmp_projects_root, "acme.com", "comb-tmpl"
    )
    staged = _stage_file(project)

    response = await comb_client.post(
        f"/ui/instances/{instance.id}/input",
        data={"text": "check", "refs": staged.name},
    )
    assert response.status_code == 200
    assert len(fake_adapter.sent_keys) == 1

    payload = fake_adapter.sent_keys[0][1]
    expected_path_part = IMAGE_PATH_TEMPLATE.format(path=str(staged))
    assert payload.startswith(expected_path_part), (
        f"Payload must start with IMAGE_PATH_TEMPLATE-formatted path, got: {payload!r}"
    )
