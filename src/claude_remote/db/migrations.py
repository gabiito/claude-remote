"""Hand-rolled migrations runner.

Algorithm:
  1. Ensure db parent dir exists.
  2. Connect to sqlite3 DB.
  3. CREATE TABLE IF NOT EXISTS schema_migrations — idempotent bootstrap.
  4. Read already-applied filenames.
  5. Glob *.sql from migrations_dir, sort lexicographically ascending.
  6. For each unapplied file:
     a. Split file content on ";" to get individual statements.
        ASSUMPTION: migration files are hand-written and contain no
        semicolons inside string literals — naive split is safe.
     b. Open a transaction (BEGIN via sqlite3's default connection behaviour
        when isolation_level is not None, using `with conn:` as context manager).
        WARNING: do NOT use sqlite3.executescript() here — it issues an implicit
        COMMIT before running, bypassing our manual transaction control.
     c. Execute each non-empty statement via cursor.execute().
     d. Insert into schema_migrations.
     e. Commit (exiting `with conn:` block commits on success, rollbacks on exception).
  7. On any exception for a file: rollback is automatic via `with conn:`, re-raise.
  8. Return list of newly applied filenames.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    """Apply pending SQL migrations from migrations_dir to db_path.

    Returns the list of filenames that were newly applied during this call.
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

        # Read already-applied set.
        applied: set[str] = {
            row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        # Collect SQL files, sorted lexicographically.
        sql_files = sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)

        newly_applied: list[str] = []

        for sql_file in sql_files:
            if sql_file.name in applied:
                continue

            sql_content = sql_file.read_text()

            # Split on ";" — safe for hand-written migration files that contain
            # no semicolons inside string literals.
            statements = [s.strip() for s in sql_content.split(";") if s.strip()]

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
