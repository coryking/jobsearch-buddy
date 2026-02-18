#!/usr/bin/env python3
"""Rich terminal UI for manual scoring of strip eval runs.

Shows original vs. stripped side-by-side with diff highlighting.
Prompts for 3 scores (1-5) per sample. Saves to CSV with resume support.

Usage:
    uv run python eval/scorer.py \
        --run eval/data/runs/v1-gpt4.1nano/ \
        --ground-truth eval/data/ground-truth/ \
        --samples eval/data/samples/
"""

from __future__ import annotations

import argparse
import csv
import difflib
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.rule import Rule
from rich.text import Text

SCORE_FIELDS = ["boilerplate_removal", "content_preservation", "no_hallucination"]
CSV_HEADER = ["filename", "run_name", *SCORE_FIELDS, "notes"]


def load_scored(scores_file: Path) -> set[tuple[str, str]]:
    """Return set of (filename, run_name) already scored."""
    if not scores_file.exists():
        return set()
    scored = set()
    with scores_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scored.add((row["filename"], row["run_name"]))
    return scored


def append_score(scores_file: Path, row: dict) -> None:
    """Append a single score row to the CSV."""
    write_header = not scores_file.exists()
    scores_file.parent.mkdir(parents=True, exist_ok=True)
    with scores_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def make_diff_text(original: str, stripped: str) -> Text:
    """Build a Rich Text showing removed lines in red, kept lines in green."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        stripped.splitlines(keepends=True),
        fromfile="original",
        tofile="stripped",
        lineterm="",
    )
    text = Text()
    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            text.append(line.rstrip() + "\n", style="bold")
        elif line.startswith("-"):
            text.append(line.rstrip() + "\n", style="red")
        elif line.startswith("+"):
            text.append(line.rstrip() + "\n", style="green")
        elif line.startswith("@@"):
            text.append(line.rstrip() + "\n", style="cyan")
        else:
            text.append(line.rstrip() + "\n")
    return text


def flag_suspicious_removals(original: str, stripped: str) -> list[str]:
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

    return suspicious[:10]  # Cap at 10


def score_sample(console: Console, filename: str, original: str, stripped: str, run_name: str) -> dict | None:
    """Show one sample and collect scores. Returns row dict or None to quit."""
    console.clear()
    console.print(Rule(f"[bold]{filename}[/bold] — run: {run_name}"))
    console.print()

    # Stats
    orig_len = len(original)
    strip_len = len(stripped)
    reduction = ((orig_len - strip_len) / orig_len * 100) if orig_len else 0
    console.print(f"Original: {orig_len:,} chars → Stripped: {strip_len:,} chars ({reduction:.0f}% reduction)")
    console.print()

    # Side by side panels (truncated for display)
    max_preview = 2000
    left = Panel(
        original[:max_preview] + ("..." if len(original) > max_preview else ""),
        title="Original",
        border_style="blue",
        width=60,
    )
    right = Panel(
        stripped[:max_preview] + ("..." if len(stripped) > max_preview else ""),
        title="Stripped",
        border_style="green",
        width=60,
    )
    console.print(Columns([left, right], padding=2))
    console.print()

    # Diff
    diff_text = make_diff_text(original, stripped)
    if diff_text.plain.strip():
        console.print(Panel(diff_text, title="Diff (red=removed, green=added)", border_style="yellow"))
    console.print()

    # Suspicious removals
    suspicious = flag_suspicious_removals(original, stripped)
    if suspicious:
        console.print("[bold red]Suspicious removals (may not be boilerplate):[/bold red]")
        for s in suspicious:
            console.print(f"  [red]- {s[:120]}[/red]")
        console.print()

    # Scoring
    console.print("[bold]Score each criterion 1-5:[/bold]")
    console.print("  1=terrible  2=poor  3=acceptable  4=good  5=excellent")
    console.print("  (q to quit, s to skip)")
    console.print()

    scores = {}
    labels = {
        "boilerplate_removal": "Boilerplate removal (did it remove the right stuff?)",
        "content_preservation": "Content preservation (did it keep the important stuff?)",
        "no_hallucination": "No hallucination (did it avoid adding/rephrasing?)",
    }

    for field in SCORE_FIELDS:
        while True:
            answer = Prompt.ask(f"  {labels[field]}")
            if answer.lower() == "q":
                return None
            if answer.lower() == "s":
                return "skip"
            try:
                val = int(answer)
                if 1 <= val <= 5:
                    scores[field] = val
                    break
            except ValueError:
                pass
            console.print("  [red]Enter 1-5, 'q' to quit, or 's' to skip[/red]")

    notes = Prompt.ask("  Notes (optional)", default="")

    return {
        "filename": filename,
        "run_name": run_name,
        **scores,
        "notes": notes,
    }


def run_scorer(
    run_dir: Path,
    ground_truth_dir: Path,
    samples_dir: Path,
    scores_file: Path,
) -> None:
    console = Console()
    run_name = run_dir.name

    # Find which files to score: intersection of ground-truth and run output
    gt_files = sorted(ground_truth_dir.glob("*.txt")) if ground_truth_dir.exists() else []
    if not gt_files:
        # No ground truth — score all run output files
        run_files = sorted(f for f in run_dir.glob("*.txt"))
        filenames = [f.name for f in run_files]
        console.print(f"[yellow]No ground-truth directory found. Scoring all {len(filenames)} run outputs.[/yellow]")
    else:
        filenames = [f.name for f in gt_files if (run_dir / f.name).exists()]
        console.print(f"Found {len(filenames)} ground-truth files with matching run output")

    if not filenames:
        console.print("[red]No matching files to score.[/red]")
        return

    # Check for already-scored
    scored = load_scored(scores_file)
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
        original_path = samples_dir / filename
        stripped_path = run_dir / filename

        if not original_path.exists():
            console.print(f"[yellow]Skipping {filename}: original not found in samples/[/yellow]")
            continue

        original = original_path.read_text(encoding="utf-8")
        stripped = stripped_path.read_text(encoding="utf-8")

        result = score_sample(console, filename, original, stripped, run_name)
        if result is None:
            console.print(f"\n[yellow]Quit after scoring {scored_count} files.[/yellow]")
            break
        if result == "skip":
            continue

        append_score(scores_file, result)
        scored_count += 1
        console.print(f"  [green]Saved! ({scored_count} scored so far)[/green]")

    console.print(f"\nTotal scored this session: {scored_count}")
    console.print(f"Scores saved to: {scores_file}")


def main():
    parser = argparse.ArgumentParser(description="Manual scorer for strip eval runs")
    parser.add_argument("--run", type=Path, required=True, help="Path to run output directory")
    parser.add_argument("--ground-truth", type=Path, default=Path("eval/data/ground-truth"),
                        help="Path to ground-truth directory (optional)")
    parser.add_argument("--samples", type=Path, default=Path("eval/data/samples"),
                        help="Path to original samples directory")
    parser.add_argument("--scores", type=Path, default=Path("eval/data/scores/manual_scores.csv"),
                        help="Path to scores CSV output")
    args = parser.parse_args()

    if not args.run.exists():
        parser.error(f"Run directory not found: {args.run}")
    if not args.samples.exists():
        parser.error(f"Samples directory not found: {args.samples}")

    run_scorer(args.run, args.ground_truth, args.samples, args.scores)


if __name__ == "__main__":
    main()
