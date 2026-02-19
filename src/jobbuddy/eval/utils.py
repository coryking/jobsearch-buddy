"""Shared helpers for eval CLI commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import questionary
import typer
from rich.console import Console
from rich.prompt import Prompt

from jobbuddy.eval.models import KNOWN_MODELS

PROMPTS_DIR = Path("eval/prompts")
RUNS_DIR = Path("eval/data/runs")


def pick_prompt(prompts_dir: Path = PROMPTS_DIR) -> Path:
    """Interactive prompt file selection via questionary.

    Prompts sorted by mtime descending; default = most recently modified.
    Excludes judge.txt.
    """
    prompt_files = sorted(
        (f for f in prompts_dir.glob("*.txt") if f.name != "judge.txt"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not prompt_files:
        print(f"No prompt files found in {prompts_dir}")
        raise typer.Exit(1)

    choices = [questionary.Choice(title=f.name, value=f) for f in prompt_files]
    selected = questionary.select(
        "Select prompt:", choices=choices, default=choices[0].value,
    ).ask()

    if selected is None:
        raise typer.Exit(1)
    return selected


def pick_models(
    prompt_stem: str,
    output_dir: Path = RUNS_DIR,
) -> list[str]:
    """Interactive multi-model selection via questionary checkboxes.

    Models that already have a run dir for this prompt are shown with a
    "done" marker and unchecked. Remaining models are listed first and
    pre-checked.
    """
    done = set()
    for m in KNOWN_MODELS:
        run_dir = output_dir / f"{prompt_stem}-{m}"
        if run_dir.exists() and any(run_dir.glob("*.txt")):
            done.add(m)

    all_models = list(KNOWN_MODELS.keys())
    remaining = [m for m in all_models if m not in done]
    completed = [m for m in all_models if m in done]

    choices: list[questionary.Choice] = []
    for m in remaining:
        choices.append(questionary.Choice(title=m, value=m, checked=True))
    for m in completed:
        choices.append(questionary.Choice(title=f"{m} (done)", value=m, checked=False))

    selected = questionary.checkbox(
        "Select models to run:", choices=choices,
    ).ask()

    if selected is None or len(selected) == 0:
        print("No models selected.")
        raise typer.Exit(1)
    return selected


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
