"""Cascade delete canary — instances on project removal.

Verifies that:
  - PRAGMA foreign_keys = ON is active on runtime connections
  - ON DELETE CASCADE on instances.project_id → projects.id works end-to-end
    through the HTTP layer

These tests are intentionally isolated from test_connection_pragma.py (which
tests the pragma at the unit level) to provide an explicit HTTP-layer canary.
If these tests fail after WU-1 through WU-6 are correct, the bug is in the
pragma fix or the migration — NOT in the test assertion.

Spec: REQ-13, S13.1
Design: §4.2
"""

from __future__ import annotations

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_payload(tmp_projects_root, name: str = "Cascade Project") -> dict:
    domain = "gh"
    project_dir = tmp_projects_root / domain / name.lower().replace(" ", "-")
    project_dir.mkdir(parents=True, exist_ok=True)
    return {"name": name, "path": str(project_dir)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_via_http(app_with_fake_tmux, tmp_projects_root, tmp_db_path):
    """Create project → launch instance → DELETE /projects/{id} → instance row gone (S13.1)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        # Create project
        r = await client.post("/projects", json=_project_payload(tmp_projects_root))
        assert r.status_code == 201
        project_id = r.json()["id"]

        # Launch instance
        r2 = await client.post(f"/projects/{project_id}/launch")
        assert r2.status_code == 201
        instance_id = r2.json()["id"]

        # Delete project via HTTP
        r3 = await client.delete(f"/projects/{project_id}")
        assert r3.status_code == 204

        # Verify instance row is gone directly via DB
        apply_migrations(tmp_db_path, MIGRATIONS_DIR)
        with get_connection_for(tmp_db_path) as conn:
            row_count = conn.execute(
                "SELECT COUNT(*) FROM instances WHERE id = ?", (instance_id,)
            ).fetchone()[0]

        assert row_count == 0, (
            f"Expected instance row to be cascade-deleted, but found {row_count} rows. "
            "Check that PRAGMA foreign_keys = ON is active on runtime connections."
        )


@pytest.mark.asyncio
async def test_cascade_delete_multiple_instances(
    app_with_fake_tmux, tmp_projects_root, tmp_db_path, fake_tmux_adapter
):
    """Two instance rows for same project → DELETE project → both rows gone."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        r = await client.post("/projects", json=_project_payload(tmp_projects_root, "Multi Instance"))
        assert r.status_code == 201
        project_id = r.json()["id"]

        # Launch first instance, stop it (creates a stopped row)
        r1 = await client.post(f"/projects/{project_id}/launch")
        assert r1.status_code == 201
        instance_id_1 = r1.json()["id"]
        session_name_1 = r1.json()["tmux_session_name"]

        await client.post(f"/instances/{instance_id_1}/stop")

        # Launch second instance (new row, different session)
        r2 = await client.post(f"/projects/{project_id}/launch")
        assert r2.status_code == 201
        instance_id_2 = r2.json()["id"]

        # Delete project
        r_del = await client.delete(f"/projects/{project_id}")
        assert r_del.status_code == 204

        # Both instance rows should be gone
        apply_migrations(tmp_db_path, MIGRATIONS_DIR)
        with get_connection_for(tmp_db_path) as conn:
            row_count = conn.execute(
                "SELECT COUNT(*) FROM instances WHERE project_id = ?", (project_id,)
            ).fetchone()[0]

        assert row_count == 0, (
            f"Expected all instance rows to be cascade-deleted, but found {row_count}."
        )


@pytest.mark.asyncio
async def test_no_cascade_on_other_project(
    app_with_fake_tmux, tmp_projects_root, tmp_db_path
):
    """Deleting project A does NOT cascade-delete project B's instances."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fake_tmux),
        base_url="http://test",
    ) as client:
        # Project A
        ra = await client.post("/projects", json=_project_payload(tmp_projects_root, "Project Alpha"))
        assert ra.status_code == 201
        project_id_a = ra.json()["id"]

        # Project B
        rb = await client.post("/projects", json=_project_payload(tmp_projects_root, "Project Beta"))
        assert rb.status_code == 201
        project_id_b = rb.json()["id"]

        # Launch an instance for each
        r_la = await client.post(f"/projects/{project_id_a}/launch")
        assert r_la.status_code == 201

        r_lb = await client.post(f"/projects/{project_id_b}/launch")
        assert r_lb.status_code == 201
        instance_id_b = r_lb.json()["id"]

        # Delete project A
        r_del = await client.delete(f"/projects/{project_id_a}")
        assert r_del.status_code == 204

        # Project B's instance must still exist
        apply_migrations(tmp_db_path, MIGRATIONS_DIR)
        with get_connection_for(tmp_db_path) as conn:
            row_count = conn.execute(
                "SELECT COUNT(*) FROM instances WHERE id = ?", (instance_id_b,)
            ).fetchone()[0]

        assert row_count == 1, (
            f"Project B's instance should NOT have been cascade-deleted, but row_count={row_count}."
        )
