"""Rich terminal UI for manual scoring of strip eval runs.

Shows original vs. stripped side-by-side with diff highlighting.
Prompts for a single 1-5 score per sample. Saves to CSV with resume support.
"""

from __future__ import annotations

import csv
import difflib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

CSV_HEADER = ["filename", "run_name", "score", "notes"]


def _load_scored(scores_file: Path) -> set[tuple[str, str]]:
    """Return set of (filename, run_name) already scored."""
    if not scores_file.exists():
        return set()
    scored = set()
    with scores_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scored.add((row["filename"], row["run_name"]))
    return scored


def _append_score(scores_file: Path, row: dict) -> None:
    """Append a single score row to the CSV."""
    write_header = not scores_file.exists()
    scores_file.parent.mkdir(parents=True, exist_ok=True)
    with scores_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _extract_removals_and_additions(original: str, stripped: str) -> tuple[list[str], list[str]]:
    """Extract contiguous blocks of removed and added text using SequenceMatcher."""
    orig_lines = original.splitlines(keepends=True)
    strip_lines = stripped.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, orig_lines, strip_lines)

    removed_blocks = []
    added_blocks = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            removed = "".join(orig_lines[i1:i2]).strip()
            added = "".join(strip_lines[j1:j2]).strip()
            if removed:
                removed_blocks.append(removed)
            if added:
                added_blocks.append(added)
        elif tag == "delete":
            removed = "".join(orig_lines[i1:i2]).strip()
            if removed:
                removed_blocks.append(removed)
        elif tag == "insert":
            added = "".join(strip_lines[j1:j2]).strip()
            if added:
                added_blocks.append(added)

    return removed_blocks, added_blocks


def _flag_suspicious_removals(original: str, stripped: str) -> list[str]:
    """Find lines removed that don't look like boilerplate."""
    orig_lines = set(original.splitlines())
    strip_lines = set(stripped.splitlines())
    removed = orig_lines - strip_lines

    suspicious = []
    boilerplate_keywords = {
        "equal opportunity", "eeo", "accommodation", "disability",
        "drug-free", "drug free", "e-verify", "background check",
        "pursuant to", "pay transparency", "we are committed to",
        "we do not discriminate", "affirmative action", "401k",
        "dental", "vision", "pto", "paid time off", "health insurance",
    }

    for line in removed:
        lower = line.lower().strip()
        if not lower or len(lower) < 20:
            continue
        if any(kw in lower for kw in boilerplate_keywords):
            continue
        suspicious.append(line.strip())

    return suspicious[:10]


def _score_sample(console: Console, filename: str, original: str, stripped: str, run_name: str) -> dict | None:
    """Show one sample and collect scores. Returns row dict, 'skip', or None to quit."""
    console.clear()
    console.print(Rule(f"[bold]{filename}[/bold] — run: {run_name}"))
    console.print()

    orig_len = len(original)
    strip_len = len(stripped)
    reduction = ((orig_len - strip_len) / orig_len * 100) if orig_len else 0
    console.print(f"Original: {orig_len:,} chars → Stripped: {strip_len:,} chars ({reduction:.0f}% reduction)")
    console.print()

    # Side-by-side diff via icdiff
    icdiff_bin = shutil.which("icdiff")
    if icdiff_bin:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="orig_", delete=False) as f_orig, \
             tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="strip_", delete=False) as f_strip:
            f_orig.write(original)
            f_strip.write(stripped)
            f_orig_path, f_strip_path = f_orig.name, f_strip.name
        try:
            cols = str(console.width or 160)
            result = subprocess.run(
                [icdiff_bin, "--cols", cols, "--label", "Original", "--label", "Stripped",
                 f_orig_path, f_strip_path],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                console.print(Rule("Side-by-side diff (icdiff)"))
                print(result.stdout)
                console.print()
        finally:
            Path(f_orig_path).unlink(missing_ok=True)
            Path(f_strip_path).unlink(missing_ok=True)
    else:
        console.print(Panel(stripped, title="Stripped Output", border_style="green"))
        console.print()

    # Removed and added blocks
    removed_blocks, added_blocks = _extract_removals_and_additions(original, stripped)

    if removed_blocks:
        removed_text = Text()
        for i, block in enumerate(removed_blocks):
            if i > 0:
                removed_text.append("\n--- block ---\n\n", style="dim")
            removed_text.append(block + "\n", style="red")
        console.print(Panel(removed_text, title=f"Removed ({len(removed_blocks)} blocks)", border_style="red"))
        console.print()

    if added_blocks:
        added_text = Text()
        for i, block in enumerate(added_blocks):
            if i > 0:
                added_text.append("\n--- block ---\n\n", style="dim")
            added_text.append(block + "\n", style="yellow")
        console.print(Panel(added_text, title=f"Added — possible hallucination ({len(added_blocks)} blocks)", border_style="yellow"))
        console.print()

    suspicious = _flag_suspicious_removals(original, stripped)
    if suspicious:
        console.print("[bold red]Suspicious removals (may not be boilerplate):[/bold red]")
        for s in suspicious:
            console.print(f"  [red]- {s[:120]}[/red]")
        console.print()

    console.print("[bold]How did it do? (1-5)[/bold]")
    console.print("  5=perfect  4=minor issues  3=decent  2=significant problems  1=failed")
    console.print("  (q to quit, s to skip)")
    console.print()

    while True:
        answer = Prompt.ask("  Score")
        if answer.lower() == "q":
            return None
        if answer.lower() == "s":
            return "skip"
        try:
            val = int(answer)
            if 1 <= val <= 5:
                score_val = val
                break
        except ValueError:
            pass
        console.print("  [red]Enter 1-5, 'q' to quit, or 's' to skip[/red]")

    notes = Prompt.ask("  Notes (optional)", default="")

    return {
        "filename": filename,
        "run_name": run_name,
        "score": score_val,
        "notes": notes,
    }


def score(
    run: Annotated[Optional[Path], typer.Option(help="Path to run output directory")] = None,
    samples: Annotated[Path, typer.Option(help="Path to original samples directory")] = Path("eval/data/samples"),
    ground_truth: Annotated[Path, typer.Option(help="Path to ground-truth directory")] = Path("eval/data/ground-truth"),
    scores: Annotated[Path, typer.Option(help="Path to scores CSV output")] = Path("eval/data/scores/manual_scores.csv"),
) -> None:
    """Manual Rich TUI scoring of strip eval runs."""
    if run is None:
        from jobbuddy.eval.utils import pick_run
        run = pick_run()
    if not run.exists():
        print(f"Run directory not found: {run}")
        raise typer.Exit(1)
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    console = Console()
    run_name = run.name

    gt_files = sorted(ground_truth.glob("*.txt")) if ground_truth.exists() else []
    if not gt_files:
        run_files = sorted(f for f in run.glob("*.txt"))
        filenames = [f.name for f in run_files]
        console.print(f"[yellow]No ground-truth directory found. Scoring all {len(filenames)} run outputs.[/yellow]")
    else:
        filenames = [f.name for f in gt_files if (run / f.name).exists()]
        console.print(f"Found {len(filenames)} ground-truth files with matching run output")

    if not filenames:
        console.print("[red]No matching files to score.[/red]")
        return

    scored = _load_scored(scores)
    remaining = [f for f in filenames if (f, run_name) not in scored]
    if len(remaining) < len(filenames):
        console.print(f"Skipping {len(filenames) - len(remaining)} already-scored files")

    if not remaining:
        console.print("[green]All files already scored![/green]")
        return

    console.print(f"Scoring {len(remaining)} files for run '{run_name}'")
    console.print("Press Enter to begin...")
    input()

    scored_count = 0
    for filename in remaining:
        original_path = samples / filename
        stripped_path = run / filename

        if not original_path.exists():
            console.print(f"[yellow]Skipping {filename}: original not found in samples/[/yellow]")
            continue

        original = original_path.read_text(encoding="utf-8")
        stripped = stripped_path.read_text(encoding="utf-8")

        result = _score_sample(console, filename, original, stripped, run_name)
        if result is None:
            console.print(f"\n[yellow]Quit after scoring {scored_count} files.[/yellow]")
            break
        if result == "skip":
            continue

        _append_score(scores, result)
        scored_count += 1
        console.print(f"  [green]Saved! ({scored_count} scored so far)[/green]")

    console.print(f"\nTotal scored this session: {scored_count}")
    console.print(f"Scores saved to: {scores}")
