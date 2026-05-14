"""Tests for /instances/* endpoints.

All tests use FakeTmuxAdapter injected via dependency_overrides.
Fixtures (app_with_fake_tmux, fake_tmux_adapter) are defined in conftest.py.

Endpoint coverage:
  POST /instances/{id}/stop  — happy, already-stopped, already-crashed,
                               not-found, session already gone
  GET  /instances            — empty, ordered newest-first, reconcile drift,
                               mixed statuses
  GET  /instances/{id}       — happy, reconcile drift, not-found,
                               404 envelope shape
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _project_payload(tmp_projects_root, name: str = "Test Project") -> dict:
    domain = "gh"
    project_dir = tmp_projects_root / domain / name.lower().replace(" ", "-")
    project_dir.mkdir(parents=True, exist_ok=True)
    return {"name": name, "path": str(project_dir)}


async def _create_project_and_launch(  # noqa: ANN001
    client, tmp_projects_root, name: str = "Test Project"
) -> tuple[str, str]:
    """Create a project and launch an instance; return (project_id, instance_id)."""
    r = await client.post("/projects", json=_project_payload(tmp_projects_root, name))
    assert r.status_code == 201
    project_id = r.json()["id"]

    r2 = await client.post(f"/projects/{project_id}/launch")
    assert r2.status_code == 201
    instance_id = r2.json()["id"]
    return project_id, instance_id


# ---------------------------------------------------------------------------
# POST /instances/{id}/stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_happy_200(app_with_fake_tmux, tmp_projects_root):
    """Running instance → POST /stop → 200, status='stopped', stopped_at non-null."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        resp = await client.post(f"/instances/{instance_id}/stop")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "stopped"
        assert body["stopped_at"] is not None
        assert body["id"] == instance_id


@pytest.mark.asyncio
async def test_stop_already_stopped_idempotent(app_with_fake_tmux, tmp_projects_root):
    """Already-stopped instance → POST /stop → 200, same record, no error (S9.2)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        # Stop once
        r1 = await client.post(f"/instances/{instance_id}/stop")
        assert r1.status_code == 200
        stopped_at_first = r1.json()["stopped_at"]

        # Stop again — idempotent
        r2 = await client.post(f"/instances/{instance_id}/stop")
        assert r2.status_code == 200
        assert r2.json()["status"] == "stopped"
        assert r2.json()["stopped_at"] == stopped_at_first  # unchanged


@pytest.mark.asyncio
async def test_stop_already_crashed_idempotent(
    app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter
):
    """Crashed instance → POST /stop → 200, same record (S9.3)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)
        # Get the session name from the launch response
        r_get = await client.get(f"/instances/{instance_id}")
        session_name = r_get.json()["tmux_session_name"]

        # Simulate crash — kill session externally, then reconcile via GET
        fake_tmux_adapter.kill_session_externally(session_name)
        r_reconcile = await client.get(f"/instances/{instance_id}")
        assert r_reconcile.json()["status"] == "crashed"

        # Now stop the crashed instance — should be idempotent
        resp = await client.post(f"/instances/{instance_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "crashed"


@pytest.mark.asyncio
async def test_stop_not_found_404(app_with_fake_tmux):
    """Non-existent instance_id → 404, code='instance_not_found' (S9.4)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.post("/instances/nonexistent-id/stop")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "instance_not_found"


@pytest.mark.asyncio
async def test_stop_session_already_gone(app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter):
    """Session externally dead, DB says running → POST /stop → 200, status='stopped' (S9.5)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        # Get session name then kill externally
        r = await client.get(f"/instances/{instance_id}")
        session_name = r.json()["tmux_session_name"]
        fake_tmux_adapter.kill_session_externally(session_name)

        # Stop should still work (kill_session returns False — idempotent)
        resp = await client.post(f"/instances/{instance_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"


# ---------------------------------------------------------------------------
# GET /instances
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(app_with_fake_tmux):
    """No instances → 200, {instances: []} (S10.1)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.get("/instances")
        assert resp.status_code == 200
        assert resp.json() == {"instances": []}


@pytest.mark.asyncio
async def test_list_ordered_newest_first(app_with_fake_tmux, tmp_projects_root):
    """Two instances → newer appears first (S10.2)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        # Create two separate projects and launch one instance each
        _, id_a = await _create_project_and_launch(client, tmp_projects_root, "Project A")
        _, id_b = await _create_project_and_launch(client, tmp_projects_root, "Project B")

        resp = await client.get("/instances")
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["instances"]]
        # B was created after A → B should appear first
        assert ids.index(id_b) < ids.index(id_a)


@pytest.mark.asyncio
async def test_list_reconciles_drift(app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter):
    """Running in DB, session marked dead → GET /instances → status='crashed' (S10.3)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        r = await client.get(f"/instances/{instance_id}")
        session_name = r.json()["tmux_session_name"]
        fake_tmux_adapter.kill_session_externally(session_name)

        resp = await client.get("/instances")
        assert resp.status_code == 200
        instance_in_list = next(i for i in resp.json()["instances"] if i["id"] == instance_id)
        assert instance_in_list["status"] == "crashed"


@pytest.mark.asyncio
async def test_list_mixed_statuses(app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter):
    """All status types appear in the list (S10.4)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        # running: project A
        _, id_running = await _create_project_and_launch(
            client, tmp_projects_root, "Project Running"
        )

        # stopped: project B — launch then stop
        _, id_stopped = await _create_project_and_launch(
            client, tmp_projects_root, "Project Stopped"
        )
        await client.post(f"/instances/{id_stopped}/stop")

        # crashed: project C — launch then kill externally, reconcile via GET
        _, id_crashed = await _create_project_and_launch(
            client, tmp_projects_root, "Project Crashed"
        )
        r = await client.get(f"/instances/{id_crashed}")
        session_name = r.json()["tmux_session_name"]
        fake_tmux_adapter.kill_session_externally(session_name)

        resp = await client.get("/instances")
        assert resp.status_code == 200
        statuses = {i["id"]: i["status"] for i in resp.json()["instances"]}
        assert statuses[id_running] == "running"
        assert statuses[id_stopped] == "stopped"
        assert statuses[id_crashed] == "crashed"


# ---------------------------------------------------------------------------
# GET /instances/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_happy_200(app_with_fake_tmux, tmp_projects_root):
    """Running instance → GET /instances/{id} → 200, full Instance JSON (S11.1)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        project_id, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        resp = await client.get(f"/instances/{instance_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == instance_id
        assert body["project_id"] == project_id
        assert body["status"] == "running"
        assert isinstance(body["tmux_session_name"], str)
        assert isinstance(body["pane_pid"], int)
        assert isinstance(body["created_at"], str)
        assert body["stopped_at"] is None


@pytest.mark.asyncio
async def test_get_reconciles_drift(app_with_fake_tmux, tmp_projects_root, fake_tmux_adapter):
    """Running in DB, session dead → GET /instances/{id} → status='crashed' (S11.2)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        _, instance_id = await _create_project_and_launch(client, tmp_projects_root)

        r = await client.get(f"/instances/{instance_id}")
        session_name = r.json()["tmux_session_name"]
        fake_tmux_adapter.kill_session_externally(session_name)

        resp = await client.get(f"/instances/{instance_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "crashed"
        assert resp.json()["stopped_at"] is not None


@pytest.mark.asyncio
async def test_get_not_found_404(app_with_fake_tmux):
    """Non-existent instance_id → 404, code='instance_not_found' (S11.3)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.get("/instances/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "instance_not_found"


@pytest.mark.asyncio
async def test_error_envelope_404_shape(app_with_fake_tmux):
    """404 response has error.code and error.message as strings; details may be absent (S12.1)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        resp = await client.get("/instances/no-such-id")
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert isinstance(err["code"], str)
        assert isinstance(err["message"], str)
        # details is absent when None (per error_response implementation)
        assert "details" not in err or err.get("details") is None
