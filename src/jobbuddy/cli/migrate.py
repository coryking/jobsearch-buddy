"""jsb migrate — apply pending database migrations."""

import sys

import psycopg
from psycopg.rows import dict_row

from jobbuddy.cli import app, console


@app.command()
def migrate():
    """Apply pending database migrations."""
    from jobbuddy.migrations import MIGRATIONS_DIR, MigrationError, apply_migrations
    from jobbuddy.settings import pg_conninfo_with_token
    from jobbuddy.store import JobStore

    conn = psycopg.connect(pg_conninfo_with_token(), autocommit=True)
    conn.row_factory = dict_row
    try:
        # Show state before applying
        all_files = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        with conn.cursor() as cur:
            cur.execute("SELECT filename FROM schema_migrations")
            already_applied = {row["filename"] for row in cur.fetchall()}

        pending = [f for f in all_files if f not in already_applied]

        console.print(
            f"[dim]{len(already_applied)} applied, "
            f"{len(pending)} pending, "
            f"{len(all_files)} total[/dim]"
        )

        if already_applied:
            console.print("[dim]Already applied:[/dim]")
            for name in sorted(already_applied):
                console.print(f"  [dim]✓ {name}[/dim]")

        if not pending:
            console.print("Database is up to date.")
        else:
            console.print("Pending:")
            for name in pending:
                console.print(f"  → {name}")
            console.print()

            try:
                applied = apply_migrations(conn)
            except MigrationError as exc:
                if exc.applied_so_far:
                    for name in exc.applied_so_far:
                        console.print(f"  [green]Applied: {name}[/green]")
                    console.print(
                        f"[green]{len(exc.applied_so_far)} migration(s) "
                        f"applied before failure.[/green]"
                    )
                    console.print()

                console.print(f"[red bold]FAILED: {exc.filename}[/red bold]")
                console.print(f"[red]{exc.cause}[/red]")
                console.print()
                console.print(
                    "[yellow]The failed migration was rolled back. "
                    "All prior migrations committed successfully.[/yellow]"
                )
                console.print(
                    f"[dim]Fix the SQL in {exc.filename} and re-run "
                    f"jsb migrate.[/dim]"
                )
                sys.exit(1)

            for name in applied:
                console.print(f"  [green]Applied: {name}[/green]")
            console.print(f"[green]{len(applied)} migration(s) applied.[/green]")

        # One-time CSV activity log import (no-ops if already imported)
        store = JobStore.__new__(JobStore)
        store.conn = conn
        store._migrate_csv_activity_log(conn)
    finally:
        conn.close()
