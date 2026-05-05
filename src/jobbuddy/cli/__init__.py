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


# Import submodules — each adds its commands to `app` via @app.command()
from jobbuddy.cli import migrate, sync, search, jobs, log, embed_test, generate_embed_text, research  # noqa: E402, F401
