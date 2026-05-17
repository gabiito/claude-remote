"""Application configuration via FastAPI dependency injection.

No lru_cache — tests override via app.dependency_overrides[get_settings].
Re-reading env vars per request is negligible cost.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    projects_root: Path
    hooks_base_url: str = "http://localhost:8000"
    # False only when the app_settings.projects_root row is NULL (first run).
    # Direct construction (tests/fixtures) defaults to configured=True.
    configured: bool = True


def get_settings() -> Settings:
    """Read settings from environment variables.

    CLAUDE_REMOTE_DB_PATH         — path to SQLite DB file (default: ./claude-remote.db)
    CLAUDE_REMOTE_PROJECTS_ROOT   — root of 2-level project hierarchy (default: ~/Projects)
    CLAUDE_REMOTE_HOOKS_BASE_URL  — base URL for hook callbacks (default: http://localhost:8000)
                                    Trailing slash is stripped. If value does not start with
                                    http:// or https://, a warning is logged.

    projects_root is always expanded and resolved to an absolute path.
    db_path is stored as-given; migrations runner does its own mkdir.
    """
    db_path = Path(os.environ.get("CLAUDE_REMOTE_DB_PATH", "./claude-remote.db"))
    env_root_explicit = "CLAUDE_REMOTE_PROJECTS_ROOT" in os.environ
    projects_root = (
        Path(os.environ.get("CLAUDE_REMOTE_PROJECTS_ROOT", "~/Projects")).expanduser().resolve()
    )
    hooks_base_url = os.environ.get(
        "CLAUDE_REMOTE_HOOKS_BASE_URL", "http://localhost:8000"
    ).rstrip("/")
    if not (hooks_base_url.startswith("http://") or hooks_base_url.startswith("https://")):
        logger.warning(
            "CLAUDE_REMOTE_HOOKS_BASE_URL=%r does not start with http:// or https://",
            hooks_base_url,
        )

    # Runtime override: app_settings.projects_root (set via /setup or
    # /settings). NULL → unconfigured → first-run setup flow. Best-effort:
    # a missing/locked DB must not break startup (treat as unconfigured).
    # Precedence: explicit CLAUDE_REMOTE_PROJECTS_ROOT env var wins (deliberate
    # deployment override). Otherwise the app_settings DB value (set via
    # /setup or /settings). Neither → unconfigured (first-run /setup).
    if env_root_explicit:
        configured = True
    else:
        configured = False
        try:
            from claude_remote.db.app_settings import (  # noqa: PLC0415
                AppSettingsRepository,
            )
            from claude_remote.db.connection import (  # noqa: PLC0415
                get_connection_for,
            )

            stored = (
                AppSettingsRepository(lambda: get_connection_for(db_path))
                .get()
                .projects_root
            )
            if stored:
                projects_root = Path(stored).expanduser().resolve()
                configured = True
        except Exception:  # noqa: BLE001 — DB absent/unmigrated/locked
            configured = False

    return Settings(
        db_path=db_path,
        projects_root=projects_root,
        hooks_base_url=hooks_base_url,
        configured=configured,
    )
