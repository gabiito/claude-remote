"""Projects deep-view router — GET /projects/{id} (HTML).

Renders a full-page view of a project when the client accepts text/html.
The JSON API route (GET /projects/{id} returning application/json) is preserved
in routes/projects.py and takes effect when the client does NOT include text/html
in Accept.

Strategy: projects_view router is registered BEFORE projects router in app.py.
When a browser requests GET /projects/{id} with Accept: text/html, this handler
runs. When an API client requests without text/html in Accept, FastAPI will NOT
match a second time against the same path — so this handler also inspects Accept
and returns a 406 if not HTML, allowing us to differentiate. However, FastAPI
only dispatches to the first matching route. Instead, we inspect the Accept header
here and delegate to the JSON implementation when the client doesn't want HTML.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.routes._views import InstanceView
from claude_remote.routes.home import build_home_cards
from claude_remote.routes.instances import get_events_repo, get_instances_repo
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services.live_status import derive_live_status
from claude_remote.services.session_grouping import build_active_sessions

router = APIRouter(tags=["projects-view"])

TERMINAL_STATUSES = {"stopped", "crashed"}


def _wants_html(request: Request) -> bool:
    """Return True if the client prefers text/html over application/json."""
    accept = request.headers.get("accept", "")
    # Browser sends Accept: text/html,application/xhtml+xml,...
    # API clients (httpx default) send Accept: */* or application/json
    # We treat */* as wanting HTML (browser-like) only when text/html appears explicitly.
    # But HTTPX default is */* — so we use a heuristic:
    # if "text/html" appears in Accept, serve HTML.
    # if Accept is absent or "*/*" only, serve JSON (API-first fallback).
    if "text/html" in accept:
        return True
    # HX-Request header is also a signal that this is an HTMX browser request
    return bool(request.headers.get("hx-request"))


@router.get("/projects/{project_id}")
async def get_project_view(
    request: Request,
    project_id: str,
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
) -> Response:
    """Render the project deep-view page for HTML clients; delegate to JSON route otherwise.

    When the client requests text/html (browser), renders the full project_view.html page.
    When the client sends API-style Accept (no text/html), falls back to the JSON representation
    by returning the project as JSON so the JSON API tests remain unaffected.

    Returns:
        200 + full project_view.html page on HTML request success.
        404 + full HTML error page if project does not exist (HTML clients).
        200 + JSON project data for non-HTML clients (API fallback).
        404 + JSON error for non-HTML clients when project not found.
    """
    if not _wants_html(request):
        # Delegate to JSON path: fetch and return JSON directly
        from claude_remote.api.errors import error_response as _err  # noqa: PLC0415

        project_obj = projects_repo.get(project_id)
        if project_obj is None:
            return _err(  # type: ignore[return-value]
                code="not_found",
                message=f"Project '{project_id}' not found",
                status_code=404,
            )
        return JSONResponse(content=project_obj.model_dump(mode="json"), status_code=200)

    project = projects_repo.get(project_id)
    if project is None:
        content = TEMPLATES.get_template("project_view_404.html").render(  # type: ignore[attr-defined]
            project_id=project_id,
        )
        return HTMLResponse(content=content, status_code=404)

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

    # Primary instance: first non-terminal by DB creation order (DESC already from repo)
    primary_instance: InstanceView | None = next(
        (iv for iv in instance_views if iv["live_status"] not in TERMINAL_STATUSES),
        None,
    )

    recent_events = events_repo.list_for_project(project_id, limit=50)

    # Vertical session-switcher rail: every project with a live console.
    active_sessions = build_active_sessions(
        build_home_cards(projects_repo, instances_repo, events_repo, now),  # pyright: ignore[reportArgumentType]
        current_project_id=project_id,
    )

    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request,
        "project_view.html",
        {
            "project": project,
            "instance_views": instance_views,
            "primary_instance": primary_instance,
            "recent_events": recent_events,
            "active_sessions": active_sessions,
        },
    )
