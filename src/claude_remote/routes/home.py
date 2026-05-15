"""Home page router — GET / renders the project list.

Returns a full HTML page (extends base.html) via Jinja2.
Context passed to template:
  - projects: list[Project]  — all projects, newest-first
  - instances_by_project: dict[str, list[Instance]]  — keyed by project.id
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.routes._templates import templates
from claude_remote.routes.instances import get_instances_repo
from claude_remote.routes.projects import get_projects_repo

router = APIRouter(tags=["ui"])


def _list_existing_domains(projects_root) -> list[str]:
    """Return immediate subdirectory names under projects_root, sorted."""
    if not projects_root.exists() or not projects_root.is_dir():
        return []
    return sorted(p.name for p in projects_root.iterdir() if p.is_dir())


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the home page with projects and their active instances."""
    projects = projects_repo.list_all()
    all_instances = instances_repo.list_all()

    instances_by_project: dict[str, list[Instance]] = {}
    for project in projects:
        instances_by_project[project.id] = []
    for instance in all_instances:
        if instance.project_id in instances_by_project:
            instances_by_project[instance.project_id].append(instance)

    return templates.TemplateResponse(  # type: ignore[return-value]
        request,
        "home.html",
        {
            "projects": projects,
            "instances_by_project": instances_by_project,
            "existing_domains": _list_existing_domains(settings.projects_root),
            "projects_root": str(settings.projects_root),
        },
    )
