from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from claude_remote.api.errors import error_response
from claude_remote.config import get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.routes import health, hooks, instances, projects, projects_view

PACKAGE_ROOT = Path(__file__).parent


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run migrations before the app accepts requests."""
    settings = get_settings()
    apply_migrations(settings.db_path, MIGRATIONS_DIR)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="claude-remote", version="0.0.1", lifespan=_lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: RequestValidationError
    ) -> HTMLResponse:
        return error_response(  # type: ignore[return-value]
            code="validation_error",
            message="Request validation failed",
            details={"errors": jsonable_encoder(exc.errors())},
            status_code=400,
        )

    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_ROOT / "static"),
        name="static",
    )
    app.include_router(health.router)
    # projects_view MUST be registered before projects so GET /projects/{id}
    # (HTML full-page view) takes priority over the JSON API route of same path.
    app.include_router(projects_view.router)
    app.include_router(projects.router)
    app.include_router(instances.router)
    app.include_router(hooks.router)

    # UI routers — imported here to avoid circular imports at module level
    # (home + ui need TEMPLATES which lives in routes/_templates.py, not app.py)
    from claude_remote.routes import home, ui  # noqa: PLC0415

    app.include_router(home.router)
    app.include_router(ui.router)
    return app


app = create_app()
