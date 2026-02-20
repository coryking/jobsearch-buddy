"""CLI entry point. Registers subcommand modules."""

import typer
from rich.console import Console

app = typer.Typer(help="Fetch job listings from ATS job boards.")
console = Console(stderr=True)

# Import submodules — each adds its commands to `app` via @app.command()
from jobbuddy.cli import sync, search, jobs, log  # noqa: E402, F401
