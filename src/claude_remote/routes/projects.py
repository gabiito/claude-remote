"""Projects API router — /projects endpoints.

Endpoints:
  POST   /projects          Create a new project
  GET    /projects          List all projects (newest first)
  GET    /projects/{id}     Get a single project by id
  DELETE /projects/{id}     Delete a project by id

All error responses use the structured envelope from api/errors.py:
  {"error": {"code": str, "message": str, "details"?: dict}}
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from claude_remote.api.errors import error_response
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.projects import (
    DuplicateProjectError,
    ProjectCreate,
    ProjectsRepository,
)
from claude_remote.services.path_validation import PathValidationError, validate_project_path
from claude_remote.services.slug import slugify

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Request / response models (HTTP layer — separate from repo models)
# ---------------------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    slug: str | None = None  # auto-generated from name when omitted


class ProjectResponse(BaseModel):
    id: str
    name: str
    slug: str
    path: str
    domain: str
    created_at: str


# ---------------------------------------------------------------------------
# DI factory for ProjectsRepository
# ---------------------------------------------------------------------------


def get_projects_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProjectsRepository:
    """Dependency provider: ProjectsRepository pointing at settings.db_path."""
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> JSONResponse:
    """Create a new project entry."""
    # 1. Validate filesystem path
    try:
        validated = validate_project_path(payload.path, settings.projects_root)
    except PathValidationError as exc:
        return error_response(
            code=exc.code,
            message=exc.message,
            status_code=400,
            details=exc.details,
        )

    # 2. Resolve slug
    slug = payload.slug or slugify(payload.name)
    if not slug:
        return error_response(
            code="empty_slug",
            message="Slug is empty after generation. Provide a non-empty 'slug' or a name "
            "that contains at least one alphanumeric character.",
            status_code=400,
        )

    # 3. Insert into DB
    project_create = ProjectCreate(
        name=payload.name,
        slug=slug,
        path=validated.absolute_path,
        domain=validated.domain,
    )
    try:
        project = repo.create(project_create=project_create)
    except DuplicateProjectError as exc:
        return error_response(
            code="conflict_domain_slug",
            message=f"A project with slug '{exc.slug}' already exists in domain '{exc.domain}'.",
            status_code=409,
            details={"domain": exc.domain, "slug": exc.slug},
        )

    return JSONResponse(
        status_code=201,
        content=ProjectResponse.model_validate(project.__dict__).model_dump(),
    )


@router.get("")
async def list_projects(
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> dict[str, object]:
    """Return all projects ordered by created_at DESC."""
    projects = repo.list_all()
    return {
        "projects": [
            ProjectResponse.model_validate(p.__dict__).model_dump() for p in projects
        ]
    }


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> JSONResponse:
    """Return a single project by id, or 404."""
    project = repo.get(project_id)
    if project is None:
        return error_response(
            code="not_found",
            message=f"Project '{project_id}' not found.",
            status_code=404,
        )
    return JSONResponse(
        status_code=200,
        content=ProjectResponse.model_validate(project.__dict__).model_dump(),
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    repo: ProjectsRepository = Depends(get_projects_repo),  # noqa: B008
) -> Response:
    """Delete a project by id. Returns 204 on success, 404 if not found."""
    deleted = repo.delete(project_id)
    if not deleted:
        return error_response(
            code="not_found",
            message=f"Project '{project_id}' not found.",
            status_code=404,
        )
    return Response(status_code=204)
