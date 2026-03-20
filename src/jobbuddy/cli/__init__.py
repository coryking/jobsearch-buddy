"""CLI entry point. Registers subcommand modules."""

import typer
from rich.console import Console

app = typer.Typer(help="Fetch job listings from ATS job boards.")
console = Console(stderr=True)


@app.callback()
def main():
    from jobbuddy.settings import get_settings

    s = get_settings()
    db = s.postgres_host or f"service={s.pg_service}"
    console.print(f"[dim]db: {db}[/dim]")


@app.command()
def migrate():
    """Apply pending database migrations."""
    import psycopg
    from psycopg.rows import dict_row

    from jobbuddy.migrations import apply_migrations
    from jobbuddy.settings import pg_conninfo_with_token

    from jobbuddy.store import JobStore

    conn = psycopg.connect(pg_conninfo_with_token(), autocommit=True)
    conn.row_factory = dict_row
    try:
        applied = apply_migrations(conn)
        if applied:
            for name in applied:
                console.print(f"  Applied: {name}")
            console.print(f"[green]{len(applied)} migration(s) applied.[/green]")
        else:
            console.print("Database is up to date.")

        # One-time CSV activity log import (no-ops if already imported)
        store = JobStore.__new__(JobStore)
        store.conn = conn
        store._migrate_csv_activity_log(conn)
    finally:
        conn.close()


# Import submodules — each adds its commands to `app` via @app.command()
from jobbuddy.cli import sync, search, jobs, log  # noqa: E402, F401
