from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from claude_remote.api.errors import error_response
from claude_remote.config import get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.routes import health, instances, projects

PACKAGE_ROOT = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=PACKAGE_ROOT / "templates")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run migrations before the app accepts requests."""
    settings = get_settings()
    apply_migrations(settings.db_path, MIGRATIONS_DIR)
    yield


async def _index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "index.html")  # type: ignore[return-value]


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
    app.include_router(projects.router)
    app.include_router(instances.router)
    app.add_api_route("/", _index, methods=["GET"], response_class=HTMLResponse)
    return app


app = create_app()
