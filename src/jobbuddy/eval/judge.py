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

from jobbuddy.eval import DEFAULT_WORKERS
from jobbuddy.eval.models import KNOWN_MODELS, ModelConfig
from rich.live import Live
from rich.table import Table
from rich.text import Text

from jobbuddy.settings import get_settings

SCORE_FIELDS = ["recall", "precision", "integrity", "fidelity"]


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 → '1.2K', 12345 → '12.3K', 500 → '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)
CSV_HEADER = ["filename", "run_name", *SCORE_FIELDS, "reasoning"]


def _load_judge_prompt(prompt_file: Path) -> str:
    return prompt_file.read_text(encoding="utf-8").strip()


def _parse_judge_response(text: str) -> dict | None:
    """Parse JSON scores from judge response. Returns None on failure."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        for field in SCORE_FIELDS:
            val = data.get(field)
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
    scores: dict[str, int] | None  # {recall: N, precision: N, ...}
    reasoning: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int
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

        usage = response.usage
        reasoning_tokens = 0
        if usage.completion_tokens_details:
            reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

        if parsed is None:
            return _JudgeResult(
                run_name=run_name, filename=run_file.name,
                scores=None, reasoning=f"PARSE ERROR: {raw[:200]}",
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                reasoning_tokens=reasoning_tokens,
                elapsed_seconds=round(elapsed, 3), error="parse_error",
            )

        return _JudgeResult(
            run_name=run_name, filename=run_file.name,
            scores={f: parsed[f] for f in SCORE_FIELDS},
            reasoning=parsed.get("reasoning", ""),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=reasoning_tokens,
            elapsed_seconds=round(elapsed, 3), error=None,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        return _JudgeResult(
            run_name=run_name, filename=run_file.name,
            scores=None, reasoning=None,
            prompt_tokens=None, completion_tokens=None,
            reasoning_tokens=0,
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
    model: Annotated[str, typer.Option(help="Judge model deployment name")] = "DeepSeek-R1-0528",
    judge_prompt: Annotated[Optional[Path], typer.Option(help="Path to judge prompt")] = None,
    workers: Annotated[int, typer.Option(help="Concurrent API workers")] = DEFAULT_WORKERS,
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
        runs_dir = Path("eval/data/runs")
        if prompt is not None:
            prompts = [prompt]
        else:
            from jobbuddy.eval.utils import pick_prompts
            prompts = pick_prompts(output_dir=runs_dir)
        run_dirs = []
        for p in prompts:
            found = _find_runs_for_prompt(p.stem, runs_dir)
            if not found:
                print(f"No runs found for prompt '{p.stem}' in {runs_dir}")
            run_dirs.extend(found)
        if not run_dirs:
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

    # Per-run tracking: total files and score accumulators
    run_totals: dict[str, int] = {}
    for rn, _, _ in work_items:
        run_totals[rn] = run_totals.get(rn, 0) + 1
    run_score_sums: dict[str, dict[str, int]] = {rn: {f: 0 for f in SCORE_FIELDS} for rn in run_totals}
    run_done_counts: dict[str, int] = {rn: 0 for rn in run_totals}

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
    running_items: set[str] = set()
    table_rows: list[tuple[str, ...]] = []
    token_totals = {"prompt": 0, "completion": 0, "reasoning": 0}

    # CSV writer with lock for thread-safe flushing
    write_header = not scores.exists()
    scores.parent.mkdir(parents=True, exist_ok=True)
    scores_file = scores.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(scores_file, fieldnames=CSV_HEADER)
    if write_header:
        writer.writeheader()
    csv_lock = threading.Lock()

    def build_display() -> Group:
        # Per-run breakdown with live means, columnar via f-string padding
        name_w = max(len(rn) for rn in run_totals)
        run_lines = []
        for rn in sorted(run_totals):
            done = run_done_counts[rn]
            t = run_totals[rn]
            progress = f"{done}/{t}".rjust(7)
            if done:
                dim_strs = []
                for f in SCORE_FIELDS:
                    m = run_score_sums[rn][f] / done
                    dim_strs.append(f"{f[0].upper()}{m:.1f}")
                run_lines.append(f"  {rn:<{name_w}}  {progress}  [green]{'/'.join(dim_strs)}[/green]")
            else:
                run_lines.append(f"  {rn:<{name_w}}  {progress}")
        header = Text.from_markup(
            f"Judging {total} files across {len(run_totals)} runs "
            f"with model={model}, workers={workers}"
        )

        queued = total - done_count - error_count - len(running_items)
        parts = []
        if running_items:
            parts.append(f"[yellow bold]\u23f3 {len(running_items)} running[/yellow bold]")
        if done_count:
            parts.append(f"[green]\u2713 {done_count} scored[/green]")
        if error_count:
            parts.append(f"[red]\u2717 {error_count} errors[/red]")
        if queued > 0:
            parts.append(f"[dim]\u00b7 {queued} queued[/dim]")
        tok_total = token_totals["prompt"] + token_totals["completion"]
        if tok_total:
            tok_parts = [f"prompt: {_fmt_tokens(token_totals['prompt'])}",
                         f"completion: {_fmt_tokens(token_totals['completion'])}"]
            if token_totals["reasoning"]:
                tok_parts.append(f"reasoning: {_fmt_tokens(token_totals['reasoning'])}")
            parts.append(f"[cyan]{' | '.join(tok_parts)}[/cyan]")
        status = Text.from_markup("  \u2502  ".join(parts))

        table = Table(show_lines=False, pad_edge=False)
        table.add_column("Run", style="dim", no_wrap=True)
        table.add_column("File", style="bold", no_wrap=True)
        table.add_column("R/P/I/F", justify="right")
        table.add_column("Time", justify="right")
        # header(1) + run lines + status(1) + table header(2) + ellipsis(1) + margin(2)
        overhead = 7 + len(run_totals)
        max_rows = max(console.height - overhead, 5)
        visible = table_rows[-max_rows:]
        if len(table_rows) > max_rows:
            table.add_row(*["..."] * len(table.columns))
        for row in visible:
            table.add_row(*row)

        run_text = Text.from_markup("\n".join(run_lines))
        return Group(header, run_text, status, table)

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

                if result.prompt_tokens:
                    token_totals["prompt"] += result.prompt_tokens
                    token_totals["completion"] += result.completion_tokens
                    token_totals["reasoning"] += result.reasoning_tokens or 0

                if result.error is None:
                    done_count += 1
                    for f in SCORE_FIELDS:
                        run_score_sums[result.run_name][f] += result.scores[f]
                    run_done_counts[result.run_name] += 1

                    with csv_lock:
                        row = {
                            "filename": result.filename,
                            "run_name": result.run_name,
                            **result.scores,
                            "reasoning": result.reasoning,
                        }
                        writer.writerow(row)
                        scores_file.flush()

                    s = result.scores
                    score_str = f"[bold]{s['recall']}/{s['precision']}/{s['integrity']}/{s['fidelity']}[/bold]"
                    table_rows.append((
                        result.run_name,
                        result.filename,
                        score_str,
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
    for rn in sorted(run_totals):
        done = run_done_counts[rn]
        if done:
            dim_strs = []
            for f in SCORE_FIELDS:
                m = run_score_sums[rn][f] / done
                dim_strs.append(f"{f}={m:.2f}")
            console.print(f"  {rn}: [green]{done} scored[/green] ({', '.join(dim_strs)})")
        else:
            console.print(f"  {rn}: [red]0 scored[/red]")
    if error_count:
        console.print(f"  [red]{error_count} total errors[/red]")
    tok_total = token_totals["prompt"] + token_totals["completion"]
    if tok_total:
        tok_parts = [f"prompt: {token_totals['prompt']:,}", f"completion: {token_totals['completion']:,}"]
        if token_totals["reasoning"]:
            tok_parts.append(f"reasoning: {token_totals['reasoning']:,}")
        console.print(f"  Tokens: {' | '.join(tok_parts)} | total: {tok_total:,}")
    console.print(f"  Scores saved to: {scores}")
