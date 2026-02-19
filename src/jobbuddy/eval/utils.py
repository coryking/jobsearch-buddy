"""Shared helpers for eval CLI commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt

RUNS_DIR = Path("eval/data/runs")


def pick_run(runs_dir: Path = RUNS_DIR) -> Path:
    """List available runs and prompt the user to pick one.

    Returns the selected run directory as a Path.
    Raises typer.Exit(1) if no runs exist or user cancels.
    """
    if not runs_dir.exists():
        print(f"Runs directory not found: {runs_dir}")
        raise typer.Exit(1)

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if not run_dirs:
        print(f"No runs found in {runs_dir}")
        raise typer.Exit(1)

    console = Console()
    console.print("\n[bold]Available runs:[/bold]")
    for i, d in enumerate(run_dirs, 1):
        file_count = len(list(d.glob("*.txt")))
        mtime = datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print(f"  {i}. {d.name}  [dim]({file_count} files, {mtime})[/dim]")
    console.print()

    while True:
        answer = Prompt.ask(f"Select run [1-{len(run_dirs)}]")
        try:
            idx = int(answer)
            if 1 <= idx <= len(run_dirs):
                return run_dirs[idx - 1]
        except ValueError:
            pass
        console.print(f"  [red]Enter 1-{len(run_dirs)}[/red]")
