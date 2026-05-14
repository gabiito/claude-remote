from fastapi import FastAPI

from claude_remote.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="claude-remote", version="0.0.1")
    app.include_router(health.router)
    return app


app = create_app()
