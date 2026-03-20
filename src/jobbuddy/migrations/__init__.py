"""Hand-rolled schema migrations for PostgreSQL.

Tracks applied migrations in a `schema_migrations` table.
Migration files are plain SQL in this directory, named NNN_description.sql.
Applied in filename order, each exactly once. Each file runs in its own
transaction — all statements + the schema_migrations bookkeeping commit
together or roll back together.

WARNING: If you're about to add features to this code (downgrades,
dependencies, dry-run, env-specific logic, etc.), STOP. Use alembic or
yoyo-migrations instead. This hand-rolled runner is intentionally minimal
and has already hit its complexity budget.
"""

import logging
from pathlib import Path

import psycopg
import psycopg.rows

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


class MigrationError(Exception):
    """Raised when a migration file fails to apply."""

    def __init__(self, filename: str, applied_so_far: list[str], cause: Exception):
        self.filename = filename
        self.applied_so_far = applied_so_far
        self.cause = cause
        super().__init__(str(cause))


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply any pending migrations. Returns list of newly applied filenames.

    Requires autocommit=True on the connection so each migration file gets
    its own transaction via conn.transaction().

    Raises MigrationError on failure with the filename that failed, the list
    of migrations that succeeded before it, and the underlying exception.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
        cur.execute("SELECT filename FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}

    migration_files = sorted(
        f for f in MIGRATIONS_DIR.glob("*.sql") if f.name not in applied
    )

    newly_applied = []
    for f in migration_files:
        sql = f.read_text()
        log.info("Applying migration: %s", f.name)
        try:
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (f.name,),
                )
        except Exception as exc:
            raise MigrationError(f.name, newly_applied, exc) from exc
        newly_applied.append(f.name)

    return newly_applied
