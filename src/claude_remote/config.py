"""Application configuration via FastAPI dependency injection.

No lru_cache — tests override via app.dependency_overrides[get_settings].
Re-reading two env vars per request is negligible cost.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    projects_root: Path


def get_settings() -> Settings:
    """Read settings from environment variables.

    CLAUDE_REMOTE_DB_PATH    — path to SQLite DB file (default: ./claude-remote.db)
    CLAUDE_REMOTE_PROJECTS_ROOT — root of 2-level project hierarchy (default: ~/Projects)

    projects_root is always expanded and resolved to an absolute path.
    db_path is stored as-given; migrations runner does its own mkdir.
    """
    db_path = Path(os.environ.get("CLAUDE_REMOTE_DB_PATH", "./claude-remote.db"))
    projects_root = (
        Path(os.environ.get("CLAUDE_REMOTE_PROJECTS_ROOT", "~/Projects")).expanduser().resolve()
    )
    return Settings(db_path=db_path, projects_root=projects_root)
