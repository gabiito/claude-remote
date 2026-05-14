"""Tests for Settings dataclass and get_settings dependency.

WU-1 — RED tests (must fail until config.py is implemented).
"""

import pytest

from claude_remote.config import Settings, get_settings


class TestSettingsDataclass:
    def test_settings_is_frozen(self, tmp_path: pytest.TempPathFactory) -> None:
        """Settings must be immutable."""
        s = Settings(db_path=tmp_path / "test.db", projects_root=tmp_path)
        with pytest.raises((AttributeError, TypeError)):
            s.db_path = tmp_path / "other.db"  # type: ignore[misc]

    def test_settings_holds_both_fields(self, tmp_path: pytest.TempPathFactory) -> None:
        db = tmp_path / "test.db"
        root = tmp_path / "projects"
        root.mkdir()
        s = Settings(db_path=db, projects_root=root)
        assert s.db_path == db
        assert s.projects_root == root


class TestGetSettings:
    def test_defaults_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no env vars are set, defaults are used."""
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        # db_path default is ./claude-remote.db (not resolved to absolute here per spec)
        assert settings.db_path.name == "claude-remote.db"
        # projects_root default resolves ~/Projects to absolute
        assert settings.projects_root.is_absolute()
        assert "Projects" in str(settings.projects_root)

    def test_db_path_env_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """CLAUDE_REMOTE_DB_PATH env var is picked up."""
        custom_db = str(tmp_path / "custom.db")
        monkeypatch.setenv("CLAUDE_REMOTE_DB_PATH", custom_db)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        assert str(settings.db_path) == custom_db

    def test_projects_root_env_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """CLAUDE_REMOTE_PROJECTS_ROOT env var is picked up and resolved."""
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", str(tmp_path))
        settings = get_settings()
        assert settings.projects_root == tmp_path.resolve()

    def test_projects_root_is_absolute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """projects_root is always absolute — no ~ in the result."""
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        assert settings.projects_root.is_absolute()
        assert "~" not in str(settings.projects_root)

    def test_projects_root_expanduser(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """A path with ~ in CLAUDE_REMOTE_PROJECTS_ROOT gets expanded."""
        # We can't guarantee ~/Projects exists but we can use HOME override
        home = tmp_path / "fakehome"
        home.mkdir()
        projects = home / "MyProjects"
        projects.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_REMOTE_PROJECTS_ROOT", "~/MyProjects")
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        settings = get_settings()
        assert settings.projects_root.is_absolute()
        assert "~" not in str(settings.projects_root)
        assert str(settings.projects_root) == str(projects)


# ---------------------------------------------------------------------------
# WU-5: hooks_base_url — RED tests
# ---------------------------------------------------------------------------


class TestHooksBaseUrl:
    def test_default_hooks_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default hooks_base_url is http://localhost:8000 when env var absent."""
        monkeypatch.delenv("CLAUDE_REMOTE_HOOKS_BASE_URL", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        assert settings.hooks_base_url == "http://localhost:8000"

    def test_env_var_overrides_hooks_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLAUDE_REMOTE_HOOKS_BASE_URL env var is picked up."""
        monkeypatch.setenv("CLAUDE_REMOTE_HOOKS_BASE_URL", "http://100.64.0.1:8000")
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        assert settings.hooks_base_url == "http://100.64.0.1:8000"

    def test_trailing_slash_stripped_from_hooks_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trailing slash is stripped from hooks_base_url on read."""
        monkeypatch.setenv("CLAUDE_REMOTE_HOOKS_BASE_URL", "http://100.64.0.1:8000/")
        monkeypatch.delenv("CLAUDE_REMOTE_DB_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_REMOTE_PROJECTS_ROOT", raising=False)
        settings = get_settings()
        assert settings.hooks_base_url == "http://100.64.0.1:8000"

    def test_settings_dataclass_has_hooks_base_url_field(self, tmp_path) -> None:
        """Settings dataclass accepts hooks_base_url field."""
        s = Settings(
            db_path=tmp_path / "test.db",
            projects_root=tmp_path,
            hooks_base_url="http://example.com:8000",
        )
        assert s.hooks_base_url == "http://example.com:8000"
