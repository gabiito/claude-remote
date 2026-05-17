import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.app_settings import AppSettings, AppSettingsRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.services.auth import hash_password, sign_session
from claude_remote.services.tmux_adapter import FakeTmuxAdapter

# Auth gate (#7) is global. The gate's own behaviour (block/allow) is fully
# covered by tests/test_auth_gate.py + test_auth_session.py + test_auth.py —
# those modules opt OUT below and exercise the real, unpatched flow. Every
# OTHER test would otherwise 303 → /login. We don't bypass the gate; we
# provision a real password + fixed session secret (exactly what
# `claudio set-password` does) and every httpx client carries a real
# HMAC-signed cookie, so the gate's verify_session runs for real.
_TEST_SECRET = "conftest-fixed-session-secret"
_TEST_PW_HASH = hash_password("conftest-pw")
_TEST_COOKIE = sign_session(_TEST_SECRET)
_AUTH_TEST_MODULES = {
    "test_auth",
    "test_auth_session",
    "test_auth_gate",
    "test_hardening",
}


@pytest.fixture(autouse=True)
def _authenticate_unless_testing_auth(request: pytest.FixtureRequest, monkeypatch):
    """Make non-auth tests pass the global gate via REAL auth (no bypass)."""
    if request.module.__name__.split(".")[-1] in _AUTH_TEST_MODULES:
        return  # the auth suites drive the gate themselves, unpatched

    _orig_get = AppSettingsRepository.get

    def _get(self: AppSettingsRepository) -> AppSettings:
        row = _orig_get(self)
        if row.password_hash is None:
            return row.model_copy(
                update={
                    "password_hash": _TEST_PW_HASH,
                    "session_secret": _TEST_SECRET,
                }
            )
        return row

    monkeypatch.setattr(AppSettingsRepository, "get", _get)

    import httpx

    _orig_init = httpx.AsyncClient.__init__

    def _init(self: httpx.AsyncClient, *a, **kw) -> None:  # type: ignore[no-untyped-def]
        _orig_init(self, *a, **kw)
        self.cookies.set("cr_session", _TEST_COOKIE)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to suppress PytestUnknownMarkWarning."""
    config.addinivalue_line(
        "markers",
        "requires_tmux: requires a real tmux binary on PATH",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-skip requires_tmux tests when tmux binary is not available."""
    if shutil.which("tmux") is None:
        skip_marker = pytest.mark.skip(reason="tmux binary not available in this environment")
        for item in items:
            if "requires_tmux" in item.keywords:
                item.add_marker(skip_marker)


@pytest.fixture()
async def async_client() -> AsyncClient:
    import dataclasses

    app = create_app()
    # This fixture runs against the real env defaults (dev DB) by design.
    # Force configured=True so the first-run /setup guard doesn't redirect
    # these shell/404/health tests; everything else stays as real settings.
    _real = get_settings()
    app.dependency_overrides[get_settings] = lambda: dataclasses.replace(
        _real, configured=True
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        yield client  # type: ignore[misc]
    app.dependency_overrides.clear()


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


# ---------------------------------------------------------------------------
# WU-5/WU-6 fixtures — FakeTmuxAdapter DI override
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_tmux_adapter() -> FakeTmuxAdapter:
    """A fresh FakeTmuxAdapter for each test."""
    return FakeTmuxAdapter()


@pytest.fixture()
def app_with_fake_tmux(app_with_overrides, fake_tmux_adapter: FakeTmuxAdapter):
    """App with FakeTmuxAdapter injected via dependency_overrides.

    Uses get_tmux_adapter as the override key so tests don't need libtmux.
    The fixture is valid only after WU-5 adds get_tmux_adapter to the routes.
    """
    from claude_remote.routes.instances import get_tmux_adapter

    app_with_overrides.dependency_overrides[get_tmux_adapter] = lambda: fake_tmux_adapter
    yield app_with_overrides


# ---------------------------------------------------------------------------
# WU-7 fixtures — events repo, settings stubs, fake settings path
# ---------------------------------------------------------------------------


@pytest.fixture()
def events_repo(tmp_db_path: Path):
    """EventsRepository backed by a migrated temp DB."""
    from claude_remote.db.connection import get_connection_for
    from claude_remote.db.events import EventsRepository
    from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

    apply_migrations(tmp_db_path, MIGRATIONS_DIR)
    return EventsRepository(
        connection_factory=lambda: get_connection_for(tmp_db_path)
    )


@pytest.fixture()
def fake_settings_path(tmp_path: Path) -> Path:
    """A path pointing to a non-existent settings file under a non-existent parent dir.

    Useful for testing apply_hooks_to_settings directory creation behaviour.
    """
    return tmp_path / ".claude" / "settings.json"


@pytest.fixture()
def settings_with_hooks_url(tmp_db_path: Path, tmp_projects_root: Path) -> "Settings":
    """Settings with hooks_base_url set to a test value."""
    return Settings(
        db_path=tmp_db_path,
        projects_root=tmp_projects_root,
        hooks_base_url="http://test-hooks.local:8000",
    )
