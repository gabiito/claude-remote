"""SQLite connection helper.

MVP-skeleton uses stdlib sqlite3 (synchronous). When concurrent writers
(hook receivers, SSE bus) land, swap this for aiosqlite — only this file
should need to change.

CRITICAL: SQLite enforces foreign keys per-connection. Every connection
opened here issues ``PRAGMA foreign_keys = ON`` so that ``ON DELETE CASCADE``
constraints are honoured at runtime (not just at migration time).
Without this pragma the FK constraint is silently ignored and orphan rows
would accumulate in the ``instances`` table after project deletion.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = "./claude-remote.db"


def _open(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite3 connection with row factory and foreign keys enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # mandatory — ON DELETE CASCADE requires this
    return conn


@contextmanager
def get_connection_for(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a connection to db_path with FK enforcement, commit/rollback, and close.

    This is the canonical connection factory for all repositories.  Tests inject
    a ``tmp_path``-based path; production code passes ``settings.db_path``.

    Args:
        db_path: Path to the SQLite database file.  Parent directories are
            created automatically.

    Yields:
        An open ``sqlite3.Connection`` with ``row_factory = sqlite3.Row`` and
        ``PRAGMA foreign_keys = ON``.  Commits on clean exit; rolls back on any
        exception.
    """
    conn = _open(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db_path() -> Path:
    return Path(os.environ.get("CLAUDE_REMOTE_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Backwards-compatible helper: opens a connection to the env-var DB path.

    Uses ``get_connection_for`` internally so FK pragma is always enabled.
    Prefer ``get_connection_for(path)`` for new code.
    """
    with get_connection_for(get_db_path()) as conn:
        yield conn
