"""CLI entry point. Registers subcommand modules."""

import sys

import typer

app = typer.Typer(help="Fetch job listings from ATS job boards.")


@app.callback()
def main():
    from jobbuddy.settings import get_settings

    s = get_settings()
    db = s.postgres_host or f"service={s.pg_service}"
    print(f"db: {db}", file=sys.stderr)


# Import submodules — each adds its commands to `app` via @app.command()
from jobbuddy.cli import migrate, sync, search, search_debug, jobs, log, research  # noqa: E402, F401
