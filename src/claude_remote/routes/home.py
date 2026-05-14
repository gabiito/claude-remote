"""Home page router — GET / renders the project list.

Returns a full HTML page (extends base.html) via Jinja2.
Context passed to template:
  - projects: list[Project]  — all projects, newest-first
  - instances_by_project: dict[str, list[Instance]]  — keyed by project.id
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from claude_remote.db.instances import Instance, InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.routes._templates import templates
from claude_remote.routes.instances import get_instances_repo
from claude_remote.routes.projects import get_projects_repo

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    projects_repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
) -> HTMLResponse:
    """Render the home page with projects and their active instances."""
    projects = projects_repo.list_all()
    all_instances = instances_repo.list_all()

    # Group instances by project_id
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
        },
    )
