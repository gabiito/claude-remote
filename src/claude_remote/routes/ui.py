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
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

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
from claude_remote.services.image_upload import (
    IMAGE_PATH_TEMPLATE,
    MAX_IMAGE_BYTES,
    UPLOAD_TTL_SECONDS,
    ImageValidationError,
    resolve_staged_ref,
    sniff_extension,
    unlink_best_effort,
    write_image,
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

logger = logging.getLogger(__name__)

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
    errors: list[str] = field(default_factory=list)  # type: ignore[assignment]


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
            message=f"Project '{project_id}' not found."
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

    # Determine if any instance is in needs_input → emit title-update HX-Trigger
    has_needs_input = any(iv["live_status"] == "needs_input" for iv in instance_views)
    extra_headers: dict[str, str] = {}
    if has_needs_input:
        extra_headers["HX-Trigger"] = json.dumps(
            {
                "title-update": {
                    "needs": True,
                    "domain": project.domain,
                    "name": project.name,
                }
            }
        )

    content = _render_project_card(request, project, instance_views, recent_events)
    return HTMLResponse(content=content, status_code=200, headers=extra_headers)


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
        return _error_fragment(request, "The 'domain' field is required.")

    if not name or not name.strip():
        return _error_fragment(request, "The 'name' field is required.")

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
    cols: int | None = Form(default=None),
    rows: int | None = Form(default=None),
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Launch an instance for a project, then navigate to its terminal.

    cols/rows (sent by the home from the device viewport) size the tmux
    window at creation so Claude renders at the right width from the first
    paint — no resize-after-the-fact, no duplicate banner. Clamped.
    """
    safe_cols = _clamp(cols, 20, 500) if cols is not None else None
    safe_rows = _clamp(rows, 5, 400) if rows is not None else None
    try:
        await asyncio.to_thread(
            lambda: launcher.launch(project_id, cols=safe_cols, rows=safe_rows)
        )
    except ProjectNotFoundError:
        return _error_fragment(
            request,
            f"Project '{project_id}' not found.",
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
        return _error_fragment(request, "Project not found.", status_code=404)

    # You launch an instance to use it → go straight to its terminal.
    # htmx XHR would silently follow a 303; a 200 + HX-Redirect makes htmx
    # do a real client-side navigation instead.
    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Redirect": f"/projects/{project_id}"},
    )


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/stop
# ---------------------------------------------------------------------------


@router.post("/instances/{instance_id}/stop", response_class=HTMLResponse)
async def stop_instance_ui(
    request: Request,
    instance_id: str,
    launcher: TmuxLauncher = Depends(get_tmux_launcher),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> HTMLResponse:
    """Stop an instance and return the updated instance row with live_status.

    Note: live_status is computed AFTER the stop so the row reflects the
    post-stop state.  Stopped instances always return ``stopped`` via
    derive_live_status (terminal status wins — Rule 1).

    Also emits an ``action-toast`` HX-Trigger so a global Alpine listener
    can surface a transient confirmation — the card itself still flips to
    the stopped pill, but the toast tells the user WHICH card it was.
    """
    import json as _json  # noqa: PLC0415

    try:
        instance = await asyncio.to_thread(lambda: launcher.stop(instance_id))
    except InstanceNotFoundError:
        return _error_fragment(
            request,
            f"Instance '{instance_id}' not found.",
            status_code=404,
        )

    now = datetime.now(UTC)
    recent_events = events_repo.list_for_instance(instance.id, limit=20)
    live_status = derive_live_status(instance, recent_events, now=now)

    # Build the action-toast payload — best-effort: project lookup must not
    # break the stop response.
    headers: dict[str, str] = {}
    try:
        project = projects_repo.get(instance.project_id)
        if project is not None:
            payload = {
                "action-toast": {
                    "message": f"✓ {project.domain}/{project.name} stopped",
                    "tone": "ok",
                }
            }
            headers["HX-Trigger"] = _json.dumps(payload)
    except Exception:  # noqa: BLE001 — toast is decoration, never block stop
        pass

    content = _render_instance_row(request, instance, live_status=live_status)
    return HTMLResponse(content=content, status_code=200, headers=headers)


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
            f"Project '{project_id}' not found.",
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
) -> Response:
    """Return the current tmux pane content as an HTML fragment.

    Polled every 2s by HTMX via ``hx-swap="innerHTML"`` on the stable
    ``<pre id="output-content">`` element.  Alpine smart-scroll handler
    stays mounted because innerHTML swap does NOT remount the element.

    Idle dedup (stateless): the body is hashed into a strong ``ETag``.
    HTMX echoes the last ETag back via ``If-None-Match``; when it matches
    (Claude finished and the pane is unchanged) we return ``204 No
    Content`` so HTMX skips the swap entirely. That leaves the terminal
    DOM — and therefore the user's text selection — untouched between
    identical 2s polls. No server-side state: the client declares what it
    already has, so it stays correct with multiple concurrent viewers.

    Returns:
        200 + escaped pane text + ``ETag`` on changed/first content.
        204 + ``ETag`` (empty body) when ``If-None-Match`` matches — HTMX
            does not swap, DOM untouched.
        200 + fallback message on adapter error (NEVER 5xx — keeps the 2s
            polling loop alive).
        404 + error fragment with ``HX-Reswap: innerHTML`` when instance missing.
    """
    instance = instances_repo.get(instance_id)
    if instance is None:
        content = TEMPLATES.get_template("partials/error_message.html").render(  # type: ignore[attr-defined]
            message=f"Instance '{instance_id}' not found."
        )
        return HTMLResponse(
            content=content,
            status_code=404,
            headers={"HX-Reswap": "innerHTML"},
        )

    try:
        raw = await asyncio.to_thread(adapter.capture_pane, instance.tmux_session_name)
        from claude_remote.services.ansi_html import convert_ansi  # noqa: PLC0415
        escaped = convert_ansi(raw)
    except TmuxOperationError:
        escaped = "[Session unavailable]"

    # Strong ETag over the exact body. blake2b(16) is fast and collision-safe
    # for change detection. Quoted per RFC 7232.
    etag = f'"{hashlib.blake2b(escaped.encode(), digest_size=16).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        # Content identical to what the client already rendered → tell HTMX
        # not to swap. 204 (not 304): HTMX skips the swap on 204 with no
        # ambiguity, so the DOM and the user's selection are left alone.
        return Response(status_code=204, headers={"ETag": etag})

    # Return ANSI-converted HTML fragment only — the <pre id="output-content"> wrapper is
    # owned by project_view.html and stays mounted (so Alpine smart-scroll
    # state survives the HTMX innerHTML swap). Returning the wrapper here
    # would nest <pre> inside <pre>, hiding the outer cr-terminal styles.
    return HTMLResponse(content=escaped, status_code=200, headers={"ETag": etag})


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
        409 + error fragment on adapter error (never 5xx — spec: Never 5xx).
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
            f"Instance '{instance_id}' not found.",
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
            status_code=409,
        )

    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": "input-sent"},
    )


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/upload-image — Upload an image and inject its path
# ---------------------------------------------------------------------------


def _safe_display_name(raw: str | None) -> str:
    """Return a display-safe filename for the chip label.

    Strips path components and truncates to 64 chars.
    This is cosmetic only — never used for path resolution.
    """
    if not raw:
        return "image"
    # Strip directory components from client-supplied name
    name = Path(raw).name or raw.split("/")[-1] or raw.split("\\")[-1] or "image"
    # Truncate for display
    return name[:64]


@router.post("/instances/{instance_id}/upload-image")
async def post_instance_upload_image(
    request: Request,
    instance_id: str,
    file: UploadFile = File(...),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> JSONResponse:
    """STAGE only — writes file, returns ref JSON. NO send_keys. NO HX-Trigger.

    Flow (v2 design, never-5xx contract):
      1. Bounded read — reject if > MAX_IMAGE_BYTES or empty.
      2. sniff_extension — magic-byte validation; Content-Type is ignored.
      3. Instance lookup — 404 fragment on miss.
      4. Project join — 404 fragment on miss (resolves Project.path).
      5. Running guard — 409 if instance is not running.
      6. write_image (asyncio.to_thread) — writes UUID-named file.
      7. Return 200 JSON {ref: <uuid>.<ext>, name: <display_name>}.

    Returns:
        200 + JSON {"ref": "<uuid>.<ext>", "name": "<display_name>"} on success.
        400 + error fragment for size/validation errors.
        404 + error fragment when instance or project not found.
        409 + error fragment when instance not running.
    """
    # Step 1: bounded read
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) == 0:
        return _error_fragment(request, "Imagen vacía.", status_code=400)  # type: ignore[return-value]
    if len(data) > MAX_IMAGE_BYTES:
        return _error_fragment(  # type: ignore[return-value]
            request,
            "Imagen demasiado grande (máx 10 MB).",
            status_code=400,
        )

    # Step 2: magic-byte validation (never trust client Content-Type)
    try:
        ext = sniff_extension(data)
    except ImageValidationError:
        return _error_fragment(  # type: ignore[return-value]
            request,
            "Tipo de imagen no soportado. Formatos válidos: PNG, JPEG, WebP, GIF.",
            status_code=400,
        )

    # Step 3: instance lookup
    instance = instances_repo.get(instance_id)
    if instance is None:
        return _error_fragment(  # type: ignore[return-value]
            request,
            f"Instance '{instance_id}' not found.",
            status_code=404,
        )

    # Step 4: project join (resolves project.path for filesystem write)
    project = projects_repo.get(instance.project_id)
    if project is None:
        return _error_fragment(  # type: ignore[return-value]
            request,
            "Project not found.",
            status_code=404,
        )

    # Step 5: running guard
    if instance.status != "running":
        return _error_fragment(  # type: ignore[return-value]
            request,
            "La instancia no está activa.",
            status_code=409,
        )

    # Step 6: write image file (off the event loop)
    path = await asyncio.to_thread(write_image, project.path, data, ext)

    # Step 7: return opaque ref — no send_keys, no HX-Trigger, no call_later
    return JSONResponse(
        {"ref": path.name, "name": _safe_display_name(file.filename)},
        status_code=200,
    )


# ---------------------------------------------------------------------------
# DELETE /ui/instances/{id}/upload-image/{ref} — Cancel a staged attachment
# ---------------------------------------------------------------------------


@router.delete("/instances/{instance_id}/upload-image/{ref}", response_class=HTMLResponse)
async def delete_instance_upload_image(
    request: Request,
    instance_id: str,
    ref: str,
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> Response:
    """Cancel a staged attachment by deleting the file.

    Containment-checked via resolve_staged_ref — never touches anything outside
    the instance's project uploads dir. Idempotent: returns 204 even if the file
    is already gone (best-effort). Never 5xx.

    Returns:
        204 on successful deletion or file already gone.
        404 if instance/project not found or ref does not resolve inside uploads dir.
    """
    # Instance lookup
    instance = instances_repo.get(instance_id)
    if instance is None:
        return _error_fragment(request, f"Instance '{instance_id}' not found.", status_code=404)

    # Project lookup
    project = projects_repo.get(instance.project_id)
    if project is None:
        return _error_fragment(request, "Project not found.", status_code=404)

    # Containment-checked resolution — rejects traversal, symlinks, foreign refs
    path = resolve_staged_ref(project.path, ref)
    if path is None:
        return _error_fragment(request, "Attachment ref not found.", status_code=404)

    # Best-effort delete (idempotent — swallows FileNotFoundError)
    unlink_best_effort(path)

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# POST /ui/instances/{id}/resize — Fit tmux window to the viewing device
# ---------------------------------------------------------------------------


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


@router.post("/instances/{instance_id}/resize", response_class=HTMLResponse)
async def post_instance_resize(
    request: Request,
    instance_id: str,
    cols: int = Form(default=80),
    rows: int = Form(default=24),
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    adapter: TmuxAdapter = Depends(get_tmux_adapter),  # noqa: B008
) -> HTMLResponse:
    """Resize the instance's tmux window so the pane re-renders fit-to-screen.

    Called fire-and-forget by the deep view when 'fit' is on (on load and on
    viewport/orientation change). cols/rows are clamped to sane tmux bounds.

    Returns:
        200 + empty body on success OR adapter error (cosmetic, never 5xx).
        404 + error fragment when the instance is not found.
    """
    instance = instances_repo.get(instance_id)
    if instance is None:
        return _error_fragment(
            request, f"Instance '{instance_id}' not found.", status_code=404
        )

    safe_cols = _clamp(cols, 20, 500)
    safe_rows = _clamp(rows, 5, 400)
    try:
        await asyncio.to_thread(
            adapter.resize_window, instance.tmux_session_name, safe_cols, safe_rows
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget; never break the UI
        logger.warning("resize_window failed for %s: %s", instance_id, exc)

    return HTMLResponse(content="", status_code=200)
