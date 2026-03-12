"""Hand-rolled schema migrations for PostgreSQL.

Tracks applied migrations in a `schema_migrations` table.
Migration files are plain SQL in this directory, named NNN_description.sql.
Applied in filename order, each exactly once.
"""

import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply any pending migrations. Returns list of newly applied filenames."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    applied = {
        row[0]
        for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }

    migration_files = sorted(
        f for f in MIGRATIONS_DIR.glob("*.sql") if f.name not in applied
    )

    newly_applied = []
    for f in migration_files:
        sql = f.read_text()
        log.info("Applying migration: %s", f.name)
        conn.execute(sql)
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)", (f.name,)
        )
        newly_applied.append(f.name)

    return newly_applied
