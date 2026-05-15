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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

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
from claude_remote.services.discovery import scan_projects_root
from claude_remote.services.exceptions import (
    InstanceAlreadyRunningError,
    InstanceNotFoundError,
    ProjectNotFoundError,
    TmuxOperationError,
)
from claude_remote.services.live_status import derive_live_status
from claude_remote.services.path_validation import PathValidationError, validate_project_path
from claude_remote.services.project_filesystem import (
    DirectoryAlreadyExistsError,
    InvalidIdentifierError,
    create_project_directory,
)
from claude_remote.services.slug import slugify
from claude_remote.services.tmux_adapter import TmuxAdapter
from claude_remote.services.tmux_launcher import TmuxLauncher

router = APIRouter(prefix="/ui", tags=["ui"])


# ---------------------------------------------------------------------------
# Sync summary dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncSummary:
    """Counts collected during a discovery sync run."""

    new: int = 0
    stale: int = 0
    unstale: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sync helper (synchronous — runs inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _run_sync(projects_root: Path, repo: ProjectsRepository) -> SyncSummary:
    """Scan filesystem and reconcile with DB.

    Best-effort per-row: a single failure does not abort the rest.

    Args:
        projects_root: Root directory to scan (2-level walk).
        repo: ProjectsRepository to read/write.

    Returns:
        SyncSummary with counts of new, stale, unstale, and errors.
    """
    candidates = scan_projects_root(projects_root)
    existing = repo.list_all()

    # Build two indexes for dedup lookups
    existing_by_pair: dict[tuple[str, str], object] = {
        (p.domain, p.slug): p for p in existing
    }
    existing_by_path: dict[str, object] = {p.path: p for p in existing}

    summary = SyncSummary()

    # Insert new candidates
    for cand in candidates:
        if (cand.domain, cand.suggested_slug) in existing_by_pair:
            continue
        if str(cand.absolute_path) in existing_by_path:
            continue
        try:
            repo.create(
                project_create=ProjectCreate(
                    name=cand.name,
                    slug=cand.suggested_slug,
                    path=cand.absolute_path,
                    domain=cand.domain,
                )
            )
            summary.new += 1
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            summary.errors.append(f"{cand.domain}/{cand.name}: {exc}")

    # Stale pass — operate on pre-sync snapshot
    for p in existing:
        path_alive = Path(p.path).exists() and Path(p.path).is_dir()
        if not path_alive and not p.is_stale:
            try:
                repo.mark_stale(p.id)
                summary.stale += 1
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"mark_stale {p.id}: {exc}")
        elif path_alive and p.is_stale:
            try:
                repo.unmark_stale(p.id)
                summary.unstale += 1
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"unmark_stale {p.id}: {exc}")

    return summary


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
# POST /ui/discovery/sync — Scan filesystem + reconcile DB
# ---------------------------------------------------------------------------


@router.post("/discovery/sync", response_class=HTMLResponse)
async def discovery_sync(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Scan projects_root, register new directories, flag missing ones as stale.

    Returns an HTMX HTML fragment (toast) summarising the sync result.
    Always includes HX-Trigger: projects-synced so the home page can reload.
    """
    summary = await asyncio.to_thread(_run_sync, settings.projects_root, projects_repo)
    content: str = TEMPLATES.get_template("partials/sync_summary.html").render(  # type: ignore[attr-defined]
        summary=summary
    )
    return HTMLResponse(
        content=content,
        status_code=200,
        headers={"HX-Trigger": "projects-synced"},
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
    create_dir: bool = Form(default=False),
    git_init: bool = Form(default=False),
    settings: Settings = Depends(get_settings),  # noqa: B008
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Create a project via HTMX form and return the project card fragment.

    Accepts `domain` + `name` from the form; composes the filesystem path as
    `<projects_root>/<domain>/<name>` and runs the standard path validation.

    Optional form fields:
      create_dir: if True, mkdir the target path before validation.
      git_init:   if True (and create_dir True), run git init in the new dir.
    """
    if not domain or not domain.strip():
        return _error_fragment(request, "El campo 'domain' es obligatorio.")

    if not name or not name.strip():
        return _error_fragment(request, "El campo 'name' es obligatorio.")

    domain_clean = domain.strip()
    name_clean = name.strip()

    # Optionally create the directory before path validation
    if create_dir:
        target = settings.projects_root / domain_clean / name_clean
        if not target.exists():
            try:
                create_project_directory(
                    settings.projects_root,
                    domain_clean,
                    name_clean,
                    git_init=git_init,
                )
            except InvalidIdentifierError as exc:
                return _error_fragment(
                    request,
                    f"Identificador inválido: {exc}",
                    status_code=400,
                )
            except DirectoryAlreadyExistsError:
                # TOCTOU race — directory appeared between exists() and mkdir.
                # Proceed to validation; treat as success.
                pass

    composed_path = settings.projects_root / domain_clean / name_clean

    try:
        validated = validate_project_path(str(composed_path), settings.projects_root)
    except PathValidationError as exc:
        return _error_fragment(request, exc.message)

    slug = slugify(name_clean)
    if not slug:
        return _error_fragment(request, "El nombre no genera un slug válido.")

    from claude_remote.db.projects import DuplicateProjectError

    try:
        project = repo.create(
            project_create=ProjectCreate(
                name=name_clean,
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

    # Return raw escaped text only — the <pre id="output-content"> wrapper is
    # owned by project_view.html and stays mounted (so Alpine smart-scroll
    # state survives the HTMX innerHTML swap). Returning the wrapper here
    # would nest <pre> inside <pre>, hiding the outer cr-terminal styles.
    return HTMLResponse(content=escaped, status_code=200)


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
