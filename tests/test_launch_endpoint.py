"""Tests for POST /projects/{project_id}/launch endpoint.

All tests use FakeTmuxAdapter injected via dependency_overrides so no
real tmux binary is needed.  The app_with_fake_tmux fixture is defined
in conftest.py (additive fixtures added for WU-5).
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient  # noqa: F401 — used in type context

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _project_payload(tmp_projects_root, name: str = "My Project") -> dict:
    """Build a valid CreateProject payload using the tmp_projects_root."""

    domain = "gh"
    project_dir = tmp_projects_root / domain / name.lower().replace(" ", "-")
    project_dir.mkdir(parents=True, exist_ok=True)

    return {
        "name": name,
        "path": str(project_dir),
    }


# ---------------------------------------------------------------------------
# Test: 201 happy path — no body (default command)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_happy_no_body(app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter):
    """POST with no body returns 201 with a running instance; adapter gets command='claude'."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        # Create project
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # Launch with no body
        resp = await client.post(f"/projects/{project_id}/launch")
        assert resp.status_code == 201

        body = resp.json()
        assert body["status"] == "running"
        assert body["project_id"] == project_id
        assert body["pane_pid"] is not None
        assert body["stopped_at"] is None

        # Adapter was called with default command
        create_calls = [c for c in fake_tmux_adapter.calls if c[0] == "create_session"]
        assert len(create_calls) == 1
        assert create_calls[0][1]["command"] == "claude"


# ---------------------------------------------------------------------------
# Test: 201 happy path — explicit command override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_happy_command_override(
    app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter
):
    """POST with explicit command passes it through to the adapter."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        resp = await client.post(
            f"/projects/{project_id}/launch",
            json={"command": "echo hi"},
        )
        assert resp.status_code == 201

        create_calls = [c for c in fake_tmux_adapter.calls if c[0] == "create_session"]
        assert len(create_calls) == 1
        assert create_calls[0][1]["command"] == "echo hi"


# ---------------------------------------------------------------------------
# Test: 404 — project not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_project_not_found(app_with_fake_tmux):
    """Non-existent project_id returns 404 with code='project_not_found'."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects/nonexistent-id/launch")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "project_not_found"


# ---------------------------------------------------------------------------
# Test: 409 — instance already running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_already_running_409(app_with_fake_tmux, tmp_projects_root):
    """Launching twice without stopping returns 409 with instance_id details."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # First launch — succeeds
        r1 = await client.post(f"/projects/{project_id}/launch")
        assert r1.status_code == 201
        running_instance_id = r1.json()["id"]

        # Second launch — conflicts
        r2 = await client.post(f"/projects/{project_id}/launch")
        assert r2.status_code == 409
        body = r2.json()
        assert body["error"]["code"] == "instance_already_running"
        assert body["error"]["details"]["instance_id"] == running_instance_id


# ---------------------------------------------------------------------------
# Test: 400 — empty command after strip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_empty_command_400(app_with_fake_tmux, tmp_projects_root):
    """Body with command='' returns 400 with code='empty_command'."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        resp = await client.post(f"/projects/{project_id}/launch", json={"command": ""})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "empty_command"


@pytest.mark.asyncio
async def test_launch_blank_command_400(app_with_fake_tmux, tmp_projects_root):
    """Body with command='   ' (whitespace only) returns 400 with code='empty_command'."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        resp = await client.post(f"/projects/{project_id}/launch", json={"command": "   "})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "empty_command"


# ---------------------------------------------------------------------------
# Test: reconciliation unblocks a 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_reconcile_unblocks_409(
    app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter
):
    """Mark session dead externally; second launch reconciles stale row and returns 201."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        r1 = await client.post(f"/projects/{project_id}/launch")
        assert r1.status_code == 201
        session_name = r1.json()["tmux_session_name"]

        # Simulate external crash — kill session without going through the API
        fake_tmux_adapter.kill_session_externally(session_name)

        # Second launch should reconcile the stale row and succeed with 201
        r2 = await client.post(f"/projects/{project_id}/launch")
        assert r2.status_code == 201
        assert r2.json()["status"] == "running"


# ---------------------------------------------------------------------------
# Test: response shape (all 7 Instance fields)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_response_shape(app_with_fake_tmux, tmp_projects_root):
    """201 response has all 7 Instance fields with correct types."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        resp = await client.post(f"/projects/{project_id}/launch")
        assert resp.status_code == 201

        body = resp.json()
        assert isinstance(body["id"], str)
        assert isinstance(body["project_id"], str)
        assert isinstance(body["tmux_session_name"], str)
        assert isinstance(body["pane_pid"], int)
        assert body["status"] == "running"
        assert isinstance(body["created_at"], str)
        assert body["stopped_at"] is None

        # Session name format: claude-remote-{slug}-{8hexchars}
        assert re.match(r"^claude-remote-[a-z0-9-]+-[0-9a-f]{8}$", body["tmux_session_name"])


# ---------------------------------------------------------------------------
# Test: 500 when adapter raises TmuxOperationError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_adapter_error_500(
    app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter
):
    """Adapter raise maps to 500 tmux_operation_failed."""
    from claude_remote.services.exceptions import TmuxOperationError

    # Make next create_session fail
    original_create = fake_tmux_adapter.create_session

    def _failing_create(name, cwd, command):
        raise TmuxOperationError("create_session", RuntimeError("tmux died"))

    fake_tmux_adapter.create_session = _failing_create  # type: ignore[method-assign]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        resp = await client.post(f"/projects/{project_id}/launch")
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "tmux_operation_failed"

    # Restore
    fake_tmux_adapter.create_session = original_create  # type: ignore[method-assign]
