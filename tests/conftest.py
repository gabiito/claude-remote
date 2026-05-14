from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations


@pytest.fixture()
async def async_client() -> AsyncClient:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        yield client  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NEW fixtures — additive (WU-5)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    """A unique DB file path per test (deleted automatically by tmp_path teardown)."""
    return tmp_path / "test.db"


@pytest.fixture()
def tmp_projects_root(tmp_path: Path) -> Path:
    """A temporary projects root directory, pre-created."""
    root = tmp_path / "projects-root"
    root.mkdir()
    return root


@pytest.fixture()
def make_fake_project(tmp_projects_root: Path):
    """Factory that creates <root>/<domain>/<project>/ and returns the absolute Path."""

    def _make(domain: str, project: str) -> Path:
        p = tmp_projects_root / domain / project
        p.mkdir(parents=True)
        return p

    return _make


@pytest.fixture()
def settings_override(tmp_db_path: Path, tmp_projects_root: Path) -> Settings:
    """A Settings instance pointing at per-test temp paths."""
    return Settings(db_path=tmp_db_path, projects_root=tmp_projects_root)


@pytest.fixture()
def app_with_overrides(
    settings_override: Settings, tmp_db_path: Path
) -> Generator:
    """App with get_settings overridden and migrations applied (bypasses lifespan)."""
    apply_migrations(tmp_db_path, MIGRATIONS_DIR)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_override
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def async_client_with_db(app_with_overrides) -> AsyncClient:  # type: ignore[misc]
    """AsyncClient backed by an app with a per-test SQLite DB."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_overrides),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        yield c  # type: ignore[misc]
