"""LLM-as-judge -- auto-scores strip eval outputs.

For each sample, sends original + stripped to the judge model and
parses structured JSON scores.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from openai import AzureOpenAI
from rich.console import Console, Group

from jobbuddy.eval.models import KNOWN_MODELS, ModelConfig
from rich.live import Live
from rich.table import Table
from rich.text import Text

from jobbuddy.settings import get_settings

CSV_HEADER = ["filename", "run_name", "score", "reasoning"]


def _load_judge_prompt(prompt_file: Path) -> str:
    return prompt_file.read_text(encoding="utf-8").strip()


def _parse_judge_response(text: str) -> dict | None:
    """Parse JSON score from judge response. Returns None on failure."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        val = data.get("score")
        if not isinstance(val, int) or val < 1 or val > 5:
            return None
        return data
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


@dataclass
class _JudgeResult:
    """Result from judging one sample."""
    run_name: str
    filename: str
    score: int | None
    reasoning: str | None
    elapsed_seconds: float
    error: str | None


def _judge_one(
    client: AzureOpenAI,
    model: str,
    prompt_text: str,
    run_name: str,
    run_file: Path,
    original_file: Path,
    running_items: set[str],
) -> _JudgeResult:
    """Judge a single sample. Runs in a worker thread."""
    key = f"{run_name}:{run_file.name}"
    running_items.add(key)

    original = original_file.read_text(encoding="utf-8")
    stripped = run_file.read_text(encoding="utf-8")

    try:
        start = time.monotonic()
        user_content = f"ORIGINAL:\n{original}\n\n---\n\nSTRIPPED:\n{stripped}"
        config = KNOWN_MODELS.get(model, ModelConfig())
        response = client.chat.completions.create(
            model=config.resolve_deployment(model),
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": user_content},
            ],
        )
        elapsed = time.monotonic() - start

        raw = response.choices[0].message.content.strip()
        parsed = _parse_judge_response(raw)

        if parsed is None:
            return _JudgeResult(
                run_name=run_name, filename=run_file.name,
                score=None, reasoning=f"PARSE ERROR: {raw[:200]}",
                elapsed_seconds=round(elapsed, 3), error="parse_error",
            )

        return _JudgeResult(
            run_name=run_name, filename=run_file.name,
            score=parsed["score"], reasoning=parsed.get("reasoning", ""),
            elapsed_seconds=round(elapsed, 3), error=None,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        return _JudgeResult(
            run_name=run_name, filename=run_file.name,
            score=None, reasoning=None,
            elapsed_seconds=round(elapsed, 3), error=str(e),
        )


def _find_runs_for_prompt(prompt_stem: str, runs_dir: Path) -> list[Path]:
    """Find all run directories matching a prompt stem."""
    return sorted(
        d for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{prompt_stem}-")
    )


def judge(
    prompt: Annotated[Optional[Path], typer.Option(help="Path to strip prompt (judges all models for it)")] = None,
    run: Annotated[Optional[Path], typer.Option(help="Path to single run directory (legacy)")] = None,
    samples: Annotated[Path, typer.Option(help="Path to original samples directory")] = Path("eval/data/samples"),
    scores: Annotated[Path, typer.Option(help="Path to scores CSV output")] = Path("eval/data/scores/judge_scores.csv"),
    model: Annotated[str, typer.Option(help="Judge model deployment name")] = "gpt-5-mini",
    judge_prompt: Annotated[Optional[Path], typer.Option(help="Path to judge prompt")] = None,
    workers: Annotated[int, typer.Option(help="Concurrent API workers")] = 5,
) -> None:
    """LLM-as-judge auto-scoring of strip eval runs.

    By default, picks a prompt and judges all model runs for it.
    Use --run to judge a single run directory instead.
    """
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    # Resolve run directories to judge
    if run is not None:
        if not run.exists():
            print(f"Run directory not found: {run}")
            raise typer.Exit(1)
        run_dirs = [run]
    else:
        if prompt is None:
            from jobbuddy.eval.utils import pick_prompt
            prompt = pick_prompt()
        prompt_stem = prompt.stem
        runs_dir = Path("eval/data/runs")
        run_dirs = _find_runs_for_prompt(prompt_stem, runs_dir)
        if not run_dirs:
            print(f"No runs found for prompt '{prompt_stem}' in {runs_dir}")
            raise typer.Exit(1)

    prompt_file = judge_prompt or Path("eval/prompts/judge.txt")
    if not prompt_file.exists():
        print(f"Judge prompt not found: {prompt_file}")
        raise typer.Exit(1)

    prompt_text = _load_judge_prompt(prompt_file)

    # Load already-judged set: {(run_name, filename)}
    already_judged: set[tuple[str, str]] = set()
    if scores.exists():
        with scores.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                already_judged.add((row["run_name"], row["filename"]))

    # Build work items across all runs
    work_items = []
    for run_dir in run_dirs:
        run_name = run_dir.name
        run_files = sorted(run_dir.glob("*.txt"))
        skipped = 0
        for run_file in run_files:
            if (run_name, run_file.name) in already_judged:
                skipped += 1
                continue
            original_path = samples / run_file.name
            if not original_path.exists():
                print(f"  SKIP {run_name}/{run_file.name} (original not found)")
                continue
            work_items.append((run_name, run_file, original_path))
        if skipped:
            print(f"Skipping {skipped} already-judged in {run_name}")

    if not work_items:
        print("All files already judged!")
        return

    console = Console()
    run_names = sorted(set(rn for rn, _, _ in work_items))
    console.print(f"Judging {len(work_items)} files across {len(run_names)} runs with model={model}, workers={workers}")
    for rn in run_names:
        count = sum(1 for r, _, _ in work_items if r == rn)
        console.print(f"  {rn}: {count} files")

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=60.0,
    )

    total = len(work_items)
    done_count = 0
    error_count = 0
    score_sum = 0
    running_items: set[str] = set()
    table_rows: list[tuple[str, ...]] = []

    # CSV writer with lock for thread-safe flushing
    write_header = not scores.exists()
    scores.parent.mkdir(parents=True, exist_ok=True)
    scores_file = scores.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(scores_file, fieldnames=CSV_HEADER)
    if write_header:
        writer.writeheader()
    csv_lock = threading.Lock()

    def build_display() -> Group:
        queued = total - done_count - error_count - len(running_items)
        parts = []
        if running_items:
            parts.append(f"[yellow bold]\u23f3 {len(running_items)} running[/yellow bold]")
        if done_count:
            mean = f" (mean={score_sum / done_count:.1f})" if done_count else ""
            parts.append(f"[green]\u2713 {done_count} scored{mean}[/green]")
        if error_count:
            parts.append(f"[red]\u2717 {error_count} errors[/red]")
        if queued > 0:
            parts.append(f"[dim]\u00b7 {queued} queued[/dim]")
        status = Text.from_markup("  \u2502  ".join(parts))

        table = Table(show_lines=False, pad_edge=False)
        table.add_column("Run", style="dim", no_wrap=True)
        table.add_column("File", style="bold", no_wrap=True)
        table.add_column("Score", justify="right")
        table.add_column("Time", justify="right")
        max_rows = max(console.height - 5, 5)
        visible = table_rows[-max_rows:]
        if len(table_rows) > max_rows:
            table.add_row(*["..."] * len(table.columns))
        for row in visible:
            table.add_row(*row)

        return Group(status, table)

    with Live(build_display(), console=console, refresh_per_second=4) as live:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for run_name, run_file, original_path in work_items:
                future = executor.submit(
                    _judge_one,
                    client, model, prompt_text, run_name,
                    run_file, original_path, running_items,
                )
                futures[future] = f"{run_name}:{run_file.name}"

            for future in as_completed(futures):
                result = future.result()
                running_items.discard(futures[future])

                if result.error is None:
                    done_count += 1
                    score_sum += result.score

                    with csv_lock:
                        writer.writerow({
                            "filename": result.filename,
                            "run_name": result.run_name,
                            "score": result.score,
                            "reasoning": result.reasoning,
                        })
                        scores_file.flush()

                    table_rows.append((
                        result.run_name,
                        result.filename,
                        f"[bold]{result.score}[/bold]",
                        f"{result.elapsed_seconds:.1f}s",
                    ))
                else:
                    error_count += 1
                    style = "[red]PARSE[/red]" if result.error == "parse_error" else "[red]ERROR[/red]"
                    table_rows.append((
                        result.run_name,
                        result.filename,
                        style,
                        f"{result.elapsed_seconds:.1f}s",
                    ))

                live.update(build_display())

    scores_file.close()

    console.print()
    console.rule("[bold]Summary[/bold]")
    console.print(f"  [green]{done_count} scored[/green], [red]{error_count} failed[/red]")
    if done_count:
        console.print(f"  Mean score: {score_sum / done_count:.2f}")
    console.print(f"  Scores saved to: {scores}")
