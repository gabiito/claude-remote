import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from claude_remote.api.errors import error_response
from claude_remote.config import get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.notifications import NotificationsRepository
from claude_remote.db.vapid_keys import VapidKeysRepository
from claude_remote.routes import health, hooks, instances, projects, projects_view

PACKAGE_ROOT = Path(__file__).parent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run migrations before the app accepts requests.

    Also ensures the VAPID keypair exists (idempotent: no-op on subsequent boots).
    Also applies CLAUDE_REMOTE_NTFY_TOPIC env-var override to the DB singleton
    row if the env var is set, so /settings always shows the effective topic.
    Startup must not crash even if any step fails.
    """
    settings = get_settings()
    apply_migrations(settings.db_path, MIGRATIONS_DIR)

    # Ensure VAPID keypair exists (generates on first boot, idempotent thereafter).
    try:
        vapid_repo = VapidKeysRepository(
            connection_factory=lambda: get_connection_for(settings.db_path)
        )
        vapid_repo.get_or_create()
    except Exception as exc:  # noqa: BLE001
        logger.warning("VAPID keygen failed at startup: %s", exc)

    if settings.ntfy_topic_override:
        try:
            repo = NotificationsRepository(
                connection_factory=lambda: get_connection_for(settings.db_path)
            )
            repo.update(ntfy_topic=settings.ntfy_topic_override)
        except Exception:  # noqa: BLE001
            pass  # startup must not crash if DB locked or migration not yet applied

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="claude-remote", version="0.0.1", lifespan=_lifespan)

    # Excluded paths: let FastAPI/Starlette default handlers serve these.
    _PASSTHROUGH_PATHS = ("/openapi.json", "/docs", "/redoc")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> HTMLResponse:
        """Custom 404 handler. Excludes API docs and static assets from HTML override."""
        if exc.status_code == 404:
            path = request.url.path
            # Let API docs, openapi schema, and static assets pass through to defaults.
            if not any(path.startswith(p) for p in _PASSTHROUGH_PATHS) and not path.startswith(
                "/static/"
            ):
                from claude_remote.routes._templates import templates as TEMPLATES  # noqa: PLC0415
                content: str = TEMPLATES.get_template("404.html").render(request=request)  # type: ignore[attr-defined]
                return HTMLResponse(content=content, status_code=404)
        # Fall through to Starlette's default HTTP exception handler for all other cases.
        raise exc  # re-raise to let Starlette handle it

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
    from claude_remote.routes import home, settings, ui  # noqa: PLC0415

    app.include_router(home.router)
    app.include_router(settings.router)
    app.include_router(ui.router)
    return app


app = create_app()
