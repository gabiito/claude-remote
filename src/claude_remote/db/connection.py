"""SQLite connection helper.

MVP-skeleton uses stdlib sqlite3 (synchronous). When concurrent writers
(hook receivers, SSE bus) land, swap this for aiosqlite — only this file
should need to change.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = "./claude-remote.db"


def get_db_path() -> Path:
    return Path(os.environ.get("CLAUDE_REMOTE_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
