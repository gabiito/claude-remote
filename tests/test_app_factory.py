from fastapi import FastAPI

from claude_remote.app import create_app


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_returns_distinct_instances() -> None:
    app1 = create_app()
    app2 = create_app()
    assert app1 is not app2
