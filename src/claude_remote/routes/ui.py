"""HTMX UI action routes — /ui/* prefix.

Endpoints:
  GET    /ui/projects/{id}/card         Render a single project card (HTMX polling target)
  POST   /ui/projects                   Create project (form-urlencoded) → project card fragment
  POST   /ui/projects/{id}/launch       Launch instance → updated project card
  POST   /ui/instances/{id}/stop        Stop instance → updated instance row
  DELETE /ui/projects/{id}              Delete project → empty 200

All responses are text/html fragments (except DELETE which returns empty body).
Errors return 4xx with HX-Reswap + HX-Retarget headers + error_message partial.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.events import Event, EventsRepository
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.routes._views import InstanceView
from claude_remote.routes.instances import (
    get_events_repo,
    get_instances_repo,
    get_tmux_adapter,
    get_tmux_launcher,
)
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services.exceptions import (
    InstanceAlreadyRunningError,
    InstanceNotFoundError,
    ProjectNotFoundError,
    TmuxOperationError,
)
from claude_remote.services.live_status import derive_live_status
from claude_remote.services.path_validation import PathValidationError, validate_project_path
from claude_remote.services.slug import slugify
from claude_remote.services.tmux_adapter import TmuxAdapter
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
    instance_views: list[InstanceView],
    recent_events: list[Event],
) -> str:
    """Render project_card partial to a string.

    Args:
        request: the current ASGI request (passed through for context).
        project: the Project record.
        instance_views: list of InstanceView dicts (instance + live_status).
        recent_events: list of up to 5 project-level recent events for the feed.
    """
    return TEMPLATES.get_template("partials/project_card.html").render(  # type: ignore[attr-defined, return-value]
        project=project,
        instance_views=instance_views,
        recent_events=recent_events,
    )


def _render_instance_row(request: Request, instance: Instance, live_status: str) -> str:
    """Render instance_row partial to a string.

    Args:
        request: the current ASGI request.
        instance: the Instance record.
        live_status: derived live status string (MUST be passed explicitly;
            never read from instance.status to avoid displaying stale DB value).
    """
    return TEMPLATES.get_template("partials/instance_row.html").render(  # type: ignore[attr-defined, return-value]
        instance=instance,
        live_status=live_status,
    )


# ---------------------------------------------------------------------------
# GET /ui/projects/{id}/card — Refresh single project card (HTMX poll target)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/card", response_class=HTMLResponse)
async def get_project_card(
    request: Request,
    project_id: str,
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
) -> HTMLResponse:
    """Render and return a single project card enriched with live_status + events.

    HTMX polls this endpoint every 5s (visibility-gated) and replaces the
    entire project card via ``hx-swap="outerHTML"``.

    Returns:
        200 + project card HTML on success.
        404 + error fragment with ``HX-Reswap: outerHTML`` if project not found.
    """
    project = projects_repo.get(project_id)
    if project is None:
        content = TEMPLATES.get_template("partials/error_message.html").render(  # type: ignore[attr-defined]
            message=f"Proyecto '{project_id}' no encontrado."
        )
        return HTMLResponse(
            content=content,
            status_code=404,
            headers={"HX-Reswap": "outerHTML"},
        )

    now = datetime.now(UTC)
    instances = instances_repo.list_by_project(project_id)
    instance_views: list[InstanceView] = [
        {
            "instance": inst,
            "live_status": derive_live_status(
                inst,
                events_repo.list_for_instance(inst.id, limit=20),
                now=now,
            ),
        }
        for inst in instances
    ]
    recent_events = events_repo.list_for_project(project_id, limit=5)

    content = _render_project_card(request, project, instance_views, recent_events)
    return HTMLResponse(content=content, status_code=200)


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

    content = _render_project_card(request, project, instance_views=[], recent_events=[])
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
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
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

    now = datetime.now(UTC)
    project_instances = instances_repo.list_by_project(project_id)
    instance_views: list[InstanceView] = [
        {
            "instance": inst,
            "live_status": derive_live_status(
                inst,
                events_repo.list_for_instance(inst.id, limit=20),
                now=now,
            ),
        }
        for inst in project_instances
    ]
    recent_events = events_repo.list_for_project(project_id, limit=5)

    content = _render_project_card(request, project, instance_views, recent_events)
    return HTMLResponse(content=content, status_code=200)


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/stop
# ---------------------------------------------------------------------------


@router.post("/instances/{instance_id}/stop", response_class=HTMLResponse)
async def stop_instance_ui(
    request: Request,
    instance_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
) -> HTMLResponse:
    """Stop an instance and return the updated instance row with live_status.

    Note: live_status is computed AFTER the stop so the row reflects the
    post-stop state.  Stopped instances always return ``stopped`` via
    derive_live_status (terminal status wins — Rule 1).
    """
    try:
        instance = await asyncio.to_thread(lambda: launcher.stop(instance_id))
    except InstanceNotFoundError:
        return _error_fragment(
            request,
            f"Instancia '{instance_id}' no encontrada.",
            status_code=404,
        )

    now = datetime.now(UTC)
    recent_events = events_repo.list_for_instance(instance.id, limit=20)
    live_status = derive_live_status(instance, recent_events, now=now)

    content = _render_instance_row(request, instance, live_status=live_status)
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


# ---------------------------------------------------------------------------
# GET /ui/instances/{id}/output — Output fragment (HTMX polling target)
# ---------------------------------------------------------------------------


@router.get("/instances/{instance_id}/output", response_class=HTMLResponse)
async def get_instance_output(
    request: Request,
    instance_id: str,
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    adapter: TmuxAdapter = Depends(get_tmux_adapter),  # noqa: B008
) -> HTMLResponse:
    """Return the current tmux pane content as an HTML fragment.

    Polled every 2s by HTMX via ``hx-swap="innerHTML"`` on the stable
    ``<pre id="output-content">`` element.  Alpine smart-scroll handler
    stays mounted because innerHTML swap does NOT remount the element.

    Returns:
        200 + ``<pre id="output-content">`` with pane text on success.
        200 + ``<pre id="output-content">`` with fallback message on adapter error
            (NEVER 5xx — keeps the 2s polling loop alive).
        404 + error fragment with ``HX-Reswap: innerHTML`` when instance missing.
    """
    import html as _html  # noqa: PLC0415 — stdlib html.escape

    instance = instances_repo.get(instance_id)
    if instance is None:
        content = TEMPLATES.get_template("partials/error_message.html").render(  # type: ignore[attr-defined]
            message=f"Instancia '{instance_id}' no encontrada."
        )
        return HTMLResponse(
            content=content,
            status_code=404,
            headers={"HX-Reswap": "innerHTML"},
        )

    try:
        raw = await asyncio.to_thread(adapter.capture_pane, instance.tmux_session_name)
        escaped = _html.escape(raw)
    except TmuxOperationError:
        escaped = "[Sesión no disponible]"

    return HTMLResponse(
        content=f'<pre class="claude-output" id="output-content">{escaped}</pre>',
        status_code=200,
    )


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/input — Deliver keystrokes to tmux pane
# ---------------------------------------------------------------------------


@router.post("/instances/{instance_id}/input", response_class=HTMLResponse)
async def post_instance_input(
    request: Request,
    instance_id: str,
    text: str | None = Form(default=None),
    send_enter: bool = Form(default=True),
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    adapter: TmuxAdapter = Depends(get_tmux_adapter),  # noqa: B008
) -> HTMLResponse:
    """Deliver text to the active tmux pane via send_keys.

    Form fields:
      text: str        — keystroke text (REQUIRED, non-empty after strip)
      send_enter: bool — append Enter after text (default True)

    Returns:
        200 + empty body + ``HX-Trigger: input-sent`` on success.
        400 + error fragment when text is empty/whitespace-only.
        404 + error fragment when instance not found.
        500 + error fragment on adapter error.
    """
    # Validate text
    stripped = (text or "").strip()
    if not stripped:
        return _error_fragment(request, "El texto no puede estar vacío.", status_code=400)

    # Validate instance exists
    instance = instances_repo.get(instance_id)
    if instance is None:
        return _error_fragment(
            request,
            f"Instancia '{instance_id}' no encontrada.",
            status_code=404,
        )

    # Send to tmux
    try:
        await asyncio.to_thread(
            adapter.send_keys, instance.tmux_session_name, stripped, send_enter=send_enter
        )
    except TmuxOperationError as exc:
        return _error_fragment(
            request,
            f"Error de tmux: {exc}",
            status_code=500,
        )

    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": "input-sent"},
    )
