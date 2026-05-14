"""Instances API router — /instances endpoints.

Endpoints:
  POST /instances/{instance_id}/stop   Stop a running instance (idempotent)
  GET  /instances                      List all instances (reconciled, newest first)
  GET  /instances/{instance_id}        Get a single instance (reconciled)

DI providers (get_tmux_adapter, get_instances_repo, get_tmux_launcher) live
here so both the projects router (POST /launch) and this router share the same
dependency graph.  Tests override get_tmux_adapter via dependency_overrides.

Note on circular imports: routes/projects.py imports get_tmux_launcher from
this module.  To break the cycle, this module does NOT import from
routes/projects.py — it defines its own get_projects_repo factory directly
(same logic; avoids the cycle).

All error responses use the structured envelope from api/errors.py.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from claude_remote.api.errors import error_response
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.services.exceptions import InstanceNotFoundError
from claude_remote.services.tmux_adapter import LibTmuxAdapter, TmuxAdapter
from claude_remote.services.tmux_launcher import TmuxLauncher

router = APIRouter(prefix="/instances", tags=["instances"])


# ---------------------------------------------------------------------------
# DI factories
# ---------------------------------------------------------------------------


def get_tmux_adapter() -> TmuxAdapter:
    """Production: return a LibTmuxAdapter.

    Tests override this with FakeTmuxAdapter via app.dependency_overrides.
    """
    return LibTmuxAdapter()  # type: ignore[return-value]


def get_instances_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstancesRepository:
    """Dependency provider: InstancesRepository pointing at settings.db_path."""
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_projects_repo_for_launcher(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProjectsRepository:
    """Dependency provider: ProjectsRepository for the launcher DI graph.

    Intentionally separate from routes/projects.py::get_projects_repo to
    avoid a circular import.  Both factories produce identical objects.
    """
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_tmux_launcher(
    adapter: TmuxAdapter = Depends(get_tmux_adapter),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo_for_launcher),  # noqa: B008
) -> TmuxLauncher:
    """Compose and return a TmuxLauncher with all dependencies injected."""
    return TmuxLauncher(
        adapter=adapter,
        instances_repo=instances_repo,
        projects_repo=projects_repo,
    )


# ---------------------------------------------------------------------------
# Internal serialiser
# ---------------------------------------------------------------------------


def _instance_response(instance: Instance) -> dict:
    """Serialise an Instance to a plain dict for JSONResponse."""
    return instance.model_dump()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{instance_id}/stop", status_code=200)
async def stop_instance(
    instance_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
) -> JSONResponse:
    """Stop a running instance.

    Idempotent: already-stopped or already-crashed instances return 200
    with their current record (locked Q2).
    """
    try:
        instance = await asyncio.to_thread(lambda: launcher.stop(instance_id))
    except InstanceNotFoundError:
        return error_response(
            code="instance_not_found",
            message=f"Instance '{instance_id}' not found.",
            status_code=404,
        )
    return JSONResponse(status_code=200, content=_instance_response(instance))


@router.get("")
async def list_instances(
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
) -> dict[str, object]:
    """Return all instances reconciled against live tmux state, newest first."""
    instances = await asyncio.to_thread(lambda: launcher.reconcile_all())
    return {"instances": [_instance_response(i) for i in instances]}


@router.get("/{instance_id}")
async def get_instance(
    instance_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
) -> JSONResponse:
    """Return a single instance reconciled, or 404 if not found."""
    instance = await asyncio.to_thread(lambda: launcher.get_with_reconcile(instance_id))
    if instance is None:
        return error_response(
            code="instance_not_found",
            message=f"Instance '{instance_id}' not found.",
            status_code=404,
        )
    return JSONResponse(status_code=200, content=_instance_response(instance))
