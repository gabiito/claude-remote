"""Home page router — GET / renders the project list enriched with live_status.

Context passed to template:
  - cards: list[ProjectCardContext] — one entry per project with:
      - project: Project
      - instance_views: list[InstanceView] — instance + live_status per instance
      - recent_events: list[Event] — up to 5 cross-instance events for feed
  - existing_domains: list[str]
  - projects_root: str
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.events import Event, EventsRepository
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.routes._templates import templates
from claude_remote.routes.instances import get_events_repo, get_instances_repo
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services.live_status import derive_live_status

router = APIRouter(tags=["ui"])


# ---------------------------------------------------------------------------
# Render-time DTOs
# ---------------------------------------------------------------------------


class InstanceView(TypedDict):
    """Thin render-time DTO: instance + derived live status."""

    instance: Instance
    live_status: str


class ProjectCardContext(TypedDict):
    """All data needed to render a single project card."""

    project: object  # Project (untyped to avoid circular import)
    instance_views: list[InstanceView]
    recent_events: list[Event]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _list_existing_domains(projects_root) -> list[str]:
    """Return immediate subdirectory names under projects_root, sorted."""
    if not projects_root.exists() or not projects_root.is_dir():
        return []
    return sorted(p.name for p in projects_root.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the home page with projects enriched with live_status and events feed."""
    now = datetime.now(UTC)
    projects = projects_repo.list_all()
    all_instances = instances_repo.list_all()

    cards: list[ProjectCardContext] = []
    for project in projects:
        project_instances = [i for i in all_instances if i.project_id == project.id]

        instance_views: list[InstanceView] = []
        for inst in project_instances:
            events = events_repo.list_for_instance(inst.id, limit=20)
            live = derive_live_status(inst, events, now=now)
            instance_views.append({"instance": inst, "live_status": live})

        recent_events = events_repo.list_for_project(project.id, limit=5)

        cards.append(
            {
                "project": project,
                "instance_views": instance_views,
                "recent_events": recent_events,
            }
        )

    return templates.TemplateResponse(  # type: ignore[return-value]
        request,
        "home.html",
        {
            "cards": cards,
            "existing_domains": _list_existing_domains(settings.projects_root),
            "projects_root": str(settings.projects_root),
        },
    )
