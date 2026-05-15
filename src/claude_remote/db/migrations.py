"""Hand-rolled migrations runner.

Algorithm:
  1. Ensure db parent dir exists.
  2. Connect to sqlite3 DB.
  3. CREATE TABLE IF NOT EXISTS schema_migrations — idempotent bootstrap.
  4. Read already-applied filenames.
  5. Glob *.sql from migrations_dir, sort lexicographically ascending.
  6. For each unapplied file:
     a. Read the file. If the first non-empty line is a `-- min-sqlite-version: X.Y.Z`
        directive and the current sqlite3.sqlite_version_info < (X, Y, Z), skip the
        file and record it as `_skipped:<filename>` in schema_migrations (idempotent).
     b. Split file content on ";" to get individual statements.
        ASSUMPTION: migration files are hand-written and contain no
        semicolons inside string literals — naive split is safe.
     c. Open a transaction (BEGIN via sqlite3's default connection behaviour
        when isolation_level is not None, using `with conn:` as context manager).
        WARNING: do NOT use sqlite3.executescript() here — it issues an implicit
        COMMIT before running, bypassing our manual transaction control.
     d. Execute each non-empty statement via cursor.execute().
     e. Insert into schema_migrations.
     f. Commit (exiting `with conn:` block commits on success, rollbacks on exception).
  7. On any exception for a file: rollback is automatic via `with conn:`, re-raise.
  8. Return list of newly applied filenames (including _skipped: entries).
"""

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(
    r"^\s*--\s*min-sqlite-version:\s*(\d+)\.(\d+)\.(\d+)\s*$",
    re.IGNORECASE,
)


def _required_sqlite_version(sql_content: str) -> tuple[int, int, int] | None:
    """Parse the optional `-- min-sqlite-version: X.Y.Z` directive.

    Only the FIRST non-empty line of the file is inspected.
    Returns a 3-tuple (major, minor, patch) when the directive is present,
    or None when the file has no version gate.
    """
    for line in sql_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _VERSION_RE.match(stripped)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
        # First non-empty line is not the directive — no gate.
        return None
    return None


def apply_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    """Apply pending SQL migrations from migrations_dir to db_path.

    Returns the list of filenames that were newly applied during this call
    (including `_skipped:<filename>` entries for version-gated files).
    Returns an empty list when all migrations are already applied (idempotent).

    Raises on SQL error — the failed migration is rolled back and NOT recorded
    in schema_migrations.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Bootstrap: ensure tracking table exists (idempotent).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()

        # Read already-applied set (includes _skipped: entries).
        applied: set[str] = {
            row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        # Collect SQL files, sorted lexicographically.
        sql_files = sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)

        newly_applied: list[str] = []

        for sql_file in sql_files:
            skip_marker = f"_skipped:{sql_file.name}"
            if sql_file.name in applied or skip_marker in applied:
                continue

            sql_content = sql_file.read_text()

            # Check optional min-sqlite-version directive.
            required = _required_sqlite_version(sql_content)
            if required is not None and sqlite3.sqlite_version_info < required:
                logger.info(
                    "Skipping migration %s (requires SQLite >= %d.%d.%d; have %s)",
                    sql_file.name,
                    *required,
                    sqlite3.sqlite_version,
                )
                with conn:
                    conn.execute(
                        "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                        (skip_marker, datetime.now(UTC).isoformat()),
                    )
                newly_applied.append(skip_marker)
                continue

            # Strip the directive comment line before splitting on ";".
            # This prevents the comment from being mistaken as a statement.
            lines = sql_content.splitlines()
            cleaned_lines: list[str] = []
            first_content = True
            for line in lines:
                if first_content and line.strip() and _VERSION_RE.match(line.strip()):
                    first_content = False
                    continue
                cleaned_lines.append(line)
                if line.strip():
                    first_content = False
            cleaned_content = "\n".join(cleaned_lines)

            # Split on ";" — safe for hand-written migration files that contain
            # no semicolons inside string literals.
            statements = [s.strip() for s in cleaned_content.split(";") if s.strip()]

            # Execute all statements + metadata insert in one transaction.
            # `with conn:` commits on clean exit and rolls back on exception.
            # Do NOT use executescript() — it auto-commits before running.
            with conn:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                    (
                        sql_file.name,
                        datetime.now(UTC).isoformat(),
                    ),
                )

            newly_applied.append(sql_file.name)

        return newly_applied

    finally:
        conn.close()
