import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from claude_remote.api.errors import error_response
from claude_remote.config import get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.vapid_keys import VapidKeysRepository
from claude_remote.routes import health, hooks, instances, projects, projects_view

PACKAGE_ROOT = Path(__file__).parent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run migrations before the app accepts requests.

    Also ensures the VAPID keypair exists (idempotent: no-op on subsequent boots).
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

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="claude-remote", version="0.0.1", lifespan=_lifespan)

    # Excluded paths: let FastAPI/Starlette default handlers serve these.
    _PASSTHROUGH_PATHS = ("/openapi.json", "/docs", "/redoc")

    # First-run guard: until projects_root is configured, redirect HTML
    # navigation to /setup. Exempt static/api/health/hooks/sw/setup so assets
    # and Claude's hook receiver keep working. Resolve settings via the
    # dependency-override map so test fixtures (which override get_settings)
    # are respected exactly as in route injection.
    _GUARD_EXEMPT = (
        "/setup",
        "/ui/setup",
        "/static",
        "/api",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/hooks",
        "/sw.js",
    )

    def _guard_exempt(path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in _GUARD_EXEMPT)

    @app.middleware("http")
    async def _require_configured(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not _guard_exempt(request.url.path):
            resolver = app.dependency_overrides.get(get_settings, get_settings)
            try:
                cfg = resolver()
            except Exception:  # noqa: BLE001 — never block on a settings error
                cfg = None
            if cfg is not None and not getattr(cfg, "configured", True):
                from fastapi.responses import RedirectResponse as _RR  # noqa: PLC0415

                return _RR("/setup", status_code=303)
        return await call_next(request)  # pyright: ignore[reportUnknownVariableType]

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        """Custom 404 page; structured envelope for every other HTTP error.

        NOTE: re-raising `exc` here does NOT fall through to Starlette's
        default handler — inside a registered exception handler it propagates
        as an unhandled error and the ServerErrorMiddleware turns it into a
        spurious 500. So non-404 statuses (405/401/403/…) must be RETURNED.
        """
        if exc.status_code == 404:
            path = request.url.path
            # Let API docs, openapi schema, and static assets pass through to defaults.
            if not any(path.startswith(p) for p in _PASSTHROUGH_PATHS) and not path.startswith(
                "/static/"
            ):
                from claude_remote.routes._templates import templates as TEMPLATES  # noqa: PLC0415
                content: str = TEMPLATES.get_template("404.html").render(request=request)  # type: ignore[attr-defined]
                return HTMLResponse(content=content, status_code=404)

        # All other HTTP errors → structured envelope with the real status,
        # preserving exc.headers (e.g. the Allow header on a 405).
        response = error_response(
            code="http_error",
            message=str(exc.detail),
            status_code=exc.status_code,
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

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

    # Push notification routes — static_sw before push to avoid /api/push/ prefix conflict
    from claude_remote.routes import push, static_sw  # noqa: PLC0415

    app.include_router(static_sw.router)
    app.include_router(push.router)

    # UI routers — imported here to avoid circular imports at module level
    # (home + ui need TEMPLATES which lives in routes/_templates.py, not app.py)
    from claude_remote.routes import home, metrics, settings, setup, ui  # noqa: PLC0415

    app.include_router(setup.router)
    app.include_router(home.router)
    app.include_router(settings.router)
    app.include_router(metrics.router)
    app.include_router(ui.router)
    return app


app = create_app()
