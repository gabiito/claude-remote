"""HTMX UI action routes — /ui/* prefix.

Endpoints:
  POST   /ui/projects                   Create project (form-urlencoded) → project card fragment
  POST   /ui/projects/{id}/launch       Launch instance → updated project card
  POST   /ui/instances/{id}/stop        Stop instance → updated instance row
  DELETE /ui/projects/{id}              Delete project → empty 200

All responses are text/html fragments (except DELETE which returns empty body).
Errors return 4xx with HX-Reswap + HX-Retarget headers + error_message partial.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.routes.instances import (
    get_instances_repo,
    get_tmux_launcher,
)
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services.exceptions import (
    InstanceAlreadyRunningError,
    InstanceNotFoundError,
    ProjectNotFoundError,
    TmuxOperationError,
)
from claude_remote.services.path_validation import PathValidationError, validate_project_path
from claude_remote.services.slug import slugify
from claude_remote.services.tmux_launcher import TmuxLauncher

router = APIRouter(prefix="/ui", tags=["ui"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _error_fragment(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    """Render error_message partial with HTMX swap headers."""
    content: str = TEMPLATES.get_template("partials/error_message.html").render(  # type: ignore[attr-defined]
        message=message
    )
    return HTMLResponse(
        content=content,
        status_code=status_code,
        headers={
            "HX-Reswap": "innerHTML",
            "HX-Retarget": "#form-error",
        },
    )


def _render_project_card(
    request: Request,
    project: object,
    instances: list[Instance],
) -> str:
    """Render project_card partial to a string."""
    return TEMPLATES.get_template("partials/project_card.html").render(  # type: ignore[attr-defined, return-value]
        project=project,
        instances=instances,
    )


def _render_instance_row(request: Request, instance: Instance) -> str:
    """Render instance_row partial to a string."""
    return TEMPLATES.get_template("partials/instance_row.html").render(  # type: ignore[attr-defined, return-value]
        instance=instance,
    )


# ---------------------------------------------------------------------------
# POST /ui/projects — Create project
# ---------------------------------------------------------------------------


@router.post("/projects", response_class=HTMLResponse)
async def create_project_ui(
    request: Request,
    name: str | None = Form(default=None),
    domain: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),  # noqa: B008
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Create a project via HTMX form and return the project card fragment.

    Accepts `domain` + `name` from the form; composes the filesystem path as
    `<projects_root>/<domain>/<name>` and runs the standard path validation.
    """
    if not domain or not domain.strip():
        return _error_fragment(request, "El campo 'domain' es obligatorio.")

    if not name or not name.strip():
        return _error_fragment(request, "El campo 'name' es obligatorio.")

    composed_path = settings.projects_root / domain.strip() / name.strip()

    try:
        validated = validate_project_path(str(composed_path), settings.projects_root)
    except PathValidationError as exc:
        return _error_fragment(request, exc.message)

    slug = slugify(name)
    if not slug:
        return _error_fragment(request, "El nombre no genera un slug válido.")

    from claude_remote.db.projects import DuplicateProjectError

    try:
        project = repo.create(
            project_create=ProjectCreate(
                name=name,
                slug=slug,
                path=validated.absolute_path,
                domain=validated.domain,
            )
        )
    except DuplicateProjectError as exc:
        return _error_fragment(
            request,
            f"Ya existe un proyecto con slug '{exc.slug}' en dominio '{exc.domain}'.",
            status_code=409,
        )

    content = _render_project_card(request, project, instances=[])
    return HTMLResponse(content=content, status_code=200)


# ---------------------------------------------------------------------------
# POST /ui/projects/{id}/launch
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/launch", response_class=HTMLResponse)
async def launch_project_ui(
    request: Request,
    project_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Launch an instance for a project and return the updated project card."""
    try:
        await asyncio.to_thread(lambda: launcher.launch(project_id))
    except ProjectNotFoundError:
        return _error_fragment(
            request,
            f"Proyecto '{project_id}' no encontrado.",
            status_code=404,
        )
    except InstanceAlreadyRunningError as exc:
        return _error_fragment(
            request,
            f"Ya hay una instancia activa (id={exc.instance_id}).",
            status_code=409,
        )
    except TmuxOperationError as exc:
        return _error_fragment(
            request,
            f"Error de tmux: {exc}",
            status_code=500,
        )

    project = projects_repo.get(project_id)
    if project is None:
        return _error_fragment(request, "Proyecto no encontrado.", status_code=404)

    all_instances = instances_repo.list_all()
    project_instances = [i for i in all_instances if i.project_id == project_id]

    content = _render_project_card(request, project, instances=project_instances)
    return HTMLResponse(content=content, status_code=200)


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/stop
# ---------------------------------------------------------------------------


@router.post("/instances/{instance_id}/stop", response_class=HTMLResponse)
async def stop_instance_ui(
    request: Request,
    instance_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
) -> HTMLResponse:
    """Stop an instance and return the updated instance row."""
    try:
        instance = await asyncio.to_thread(lambda: launcher.stop(instance_id))
    except InstanceNotFoundError:
        return _error_fragment(
            request,
            f"Instancia '{instance_id}' no encontrada.",
            status_code=404,
        )

    content = _render_instance_row(request, instance)
    return HTMLResponse(content=content, status_code=200)


# ---------------------------------------------------------------------------
# DELETE /ui/projects/{id}
# ---------------------------------------------------------------------------


@router.delete("/projects/{project_id}", response_class=HTMLResponse)
async def delete_project_ui(
    request: Request,
    project_id: str,
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
) -> HTMLResponse:
    """Delete a project + kill its tmux sessions. Returns empty 200."""
    await asyncio.to_thread(lambda: launcher.kill_all_for_project(project_id))
    deleted = repo.delete(project_id)
    if not deleted:
        return _error_fragment(
            request,
            f"Proyecto '{project_id}' no encontrado.",
            status_code=404,
        )
    return HTMLResponse(content="", status_code=200)
