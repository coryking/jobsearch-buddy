"""Run a prompt+model combination against all samples.

Self-contained LLM client -- reads Azure credentials from settings,
calls the API, measures timing, writes stripped output + run_stats.csv.
"""

from __future__ import annotations

import csv
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from openai import AzureOpenAI
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from jobbuddy.eval.models import KNOWN_MODELS, ModelConfig
from jobbuddy.eval.utils import PROMPTS_DIR, pick_models, pick_prompts
from jobbuddy.settings import get_settings


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 → '1.2K', 12345 → '12.3K', 500 → '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


_CSV_COLUMNS = [
    "filename", "input_chars", "output_chars", "prompt_tokens",
    "completion_tokens", "reasoning_tokens", "total_tokens", "elapsed_seconds",
]


def _append_run_stats(csv_path: Path, row: dict) -> None:
    """Append one row to run_stats.csv, writing header if needed."""
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


@dataclass
class _SampleResult:
    """Result from processing one sample file."""
    model: str
    run_name: str
    index: int
    filename: str
    input_chars: int
    output_chars: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int
    total_tokens: int | None
    elapsed_seconds: float
    reduction: float | None
    error: str | None


def run(
    run_name: Annotated[Optional[str], typer.Option(help="Name for this run (becomes output subdir). Default: {prompt_stem}-{model}")] = None,
    prompt: Annotated[Optional[Path], typer.Option(help="Path to prompt text file")] = None,
    model: Annotated[Optional[str], typer.Option(help="Azure OpenAI model deployment name")] = None,
    samples: Annotated[Path, typer.Option(help="Samples directory")] = Path("eval/data/samples"),
    output: Annotated[Path, typer.Option(help="Base output directory for runs")] = Path("eval/data/runs"),
    workers: Annotated[int, typer.Option(help="Concurrent API workers")] = 50,
    force: Annotated[bool, typer.Option(help="Re-run all samples, ignoring existing outputs")] = False,
) -> None:
    """Run strip eval: prompt+model against samples."""
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    sample_files = sorted(
        f for f in samples.glob("*.txt")
        if f.name != "sample_manifest.json"
    )
    if not sample_files:
        print(f"No .txt samples found in {samples}")
        raise typer.Exit(1)

    # Resolve prompt(s): explicit --prompt → single, else checkbox picker
    if prompt is not None:
        if not prompt.exists():
            print(f"Prompt file not found: {prompt}")
            raise typer.Exit(1)
        prompts = [prompt]
    else:
        prompts = pick_prompts(PROMPTS_DIR, output)

    # Resolve models once for all prompts
    if model is None:
        all_models = pick_models(prompts[0].stem, output)
    else:
        all_models = [model]

    # Build one flat queue across all prompts × models × samples
    work_items: list[dict] = []
    skipped = 0

    for prompt_file in prompts:
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()

        for m in all_models:
            config = KNOWN_MODELS.get(m, ModelConfig())
            name = run_name if run_name else f"{prompt_file.stem}-{m}"
            output_dir = output / name
            output_dir.mkdir(parents=True, exist_ok=True)
            for i, sample_file in enumerate(sample_files, 1):
                if not force and (output_dir / sample_file.name).exists():
                    skipped += 1
                    continue
                work_items.append({
                    "model": m,
                    "config": config,
                    "run_name": name,
                    "output_dir": output_dir,
                    "sample_file": sample_file,
                    "prompt_text": prompt_text,
                    "index": i,
                })

    if skipped:
        print(f"Skipping {skipped} already-completed samples")

    if not work_items:
        print("All samples already completed!")
        raise typer.Exit(0)

    _run_all(work_items, sample_files, all_models, output, workers)


def _process_sample(
    client: AzureOpenAI,
    model: str,
    config: ModelConfig,
    prompt_text: str,
    sample_file: Path,
    output_dir: Path,
    run_name: str,
    index: int,
    running_items: set[str],
) -> _SampleResult:
    """Process a single sample file. Runs in a worker thread."""
    key = f"{model}:{sample_file.name}"
    running_items.add(key)
    description = sample_file.read_text(encoding="utf-8")
    input_chars = len(description)

    try:
        start = time.monotonic()
        response = client.chat.completions.create(
            model=config.resolve_deployment(model),
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": description},
            ],
            **config.api_params,
        )
        elapsed = time.monotonic() - start

        result_text = response.choices[0].message.content.strip()
        usage = response.usage

        out_file = output_dir / sample_file.name
        out_file.write_text(result_text, encoding="utf-8")

        reduction = ((input_chars - len(result_text)) / input_chars * 100) if input_chars else 0

        reasoning_tokens = 0
        if usage.completion_tokens_details:
            reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

        return _SampleResult(
            model=model,
            run_name=run_name,
            index=index,
            filename=sample_file.name,
            input_chars=input_chars,
            output_chars=len(result_text),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=usage.total_tokens,
            elapsed_seconds=round(elapsed, 3),
            reduction=reduction,
            error=None,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        return _SampleResult(
            model=model,
            run_name=run_name,
            index=index,
            filename=sample_file.name,
            input_chars=input_chars,
            output_chars=None,
            prompt_tokens=None,
            completion_tokens=None,
            reasoning_tokens=0,
            total_tokens=None,
            elapsed_seconds=round(elapsed, 3),
            reduction=None,
            error=str(e),
        )


def _run_all(
    work_items: list[dict],
    sample_files: list[Path],
    models: list[str],
    output_base: Path,
    workers: int,
) -> None:
    console = Console()
    # Show model/run column when there are multiple run_name combos
    run_name_set = {item["run_name"] for item in work_items}
    multi_model = len(run_name_set) > 1

    for m in models:
        config = KNOWN_MODELS.get(m, ModelConfig())
        deployment = config.resolve_deployment(m)
        params_str = ", ".join(f"{k}={v}" for k, v in config.api_params.items()) if config.api_params else "defaults"
        if deployment != m:
            params_str = f"deployment={deployment}, {params_str}"
        console.print(f"  {m} ({params_str})")
    console.print(f"{len(work_items)} total items ({len(sample_files)} samples x {len(run_name_set)} runs), workers={workers}")

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
    # Per-run_name collectors (prompt×model combo)
    run_names_seen: list[str] = []
    run_stats: dict[str, list[dict]] = {}
    run_errors: dict[str, list[dict]] = {}
    for item in work_items:
        rn = item["run_name"]
        if rn not in run_stats:
            run_names_seen.append(rn)
            run_stats[rn] = []
            run_errors[rn] = []
    table_rows: list[tuple[str, ...]] = []
    running_items: set[str] = set()
    token_totals = {"prompt": 0, "completion": 0, "reasoning": 0}
    # Per-model live accumulators
    model_tokens: dict[str, dict[str, int]] = {}
    model_latencies: dict[str, list[float]] = {}
    model_done: dict[str, int] = {}
    model_total: dict[str, int] = {}
    for item in work_items:
        m = item["model"]
        if m not in model_tokens:
            model_tokens[m] = {"prompt": 0, "completion": 0, "reasoning": 0}
            model_latencies[m] = []
            model_done[m] = 0
            model_total[m] = 0
        model_total[m] += 1

    def build_display() -> Group:
        # Per-model stats block
        name_w = max(len(m) for m in model_tokens)
        model_lines = []
        for m in model_tokens:
            done_m = model_done[m]
            total_m = model_total[m]
            progress = f"{done_m}/{total_m}".rjust(7)
            if done_m:
                mt = model_tokens[m]
                tok_str = f"tok: {_fmt_tokens(mt['prompt'] + mt['completion'])}"
                lat = model_latencies[m]
                avg_lat = f"avg: {statistics.mean(lat):.1f}s"
                model_lines.append(f"  {m:<{name_w}}  {progress}  [cyan]{tok_str}[/cyan]  [dim]{avg_lat}[/dim]")
            else:
                model_lines.append(f"  {m:<{name_w}}  {progress}")

        queued = total - done_count - error_count - len(running_items)
        parts = []
        if running_items:
            parts.append(f"[yellow bold]\u23f3 {len(running_items)} running[/yellow bold]")
        if done_count:
            parts.append(f"[green]\u2713 {done_count} done[/green]")
        if error_count:
            parts.append(f"[red]\u2717 {error_count} errors[/red]")
        if queued > 0:
            parts.append(f"[dim]\u00b7 {queued} queued[/dim]")
        status = Text.from_markup("  \u2502  ".join(parts))

        table = Table(show_lines=False, pad_edge=False)
        table.add_column("#", justify="right", style="dim", width=4)
        if multi_model:
            table.add_column("Run", style="cyan", no_wrap=True)
        table.add_column("File", style="bold", no_wrap=True)
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Reduc", justify="right")
        table.add_column("Time", justify="right")
        table.add_column("Tokens", justify="right")
        # model_lines + status(1) + table header(2) + ellipsis(1) + margin(2)
        overhead = 6 + len(model_tokens)
        max_rows = max(console.height - overhead, 5)
        visible = table_rows[-max_rows:]
        if len(table_rows) > max_rows:
            table.add_row(*["..."] * len(table.columns))
        for row in visible:
            table.add_row(*row)

        model_text = Text.from_markup("\n".join(model_lines))
        return Group(model_text, status, table)

    with Live(build_display(), console=console, refresh_per_second=4) as live:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for item in work_items:
                future = executor.submit(
                    _process_sample,
                    client, item["model"], item["config"], item["prompt_text"],
                    item["sample_file"], item["output_dir"], item["run_name"],
                    item["index"], running_items,
                )
                key = f"{item['model']}:{item['sample_file'].name}"
                futures[future] = key

            for future in as_completed(futures):
                result = future.result()
                running_items.discard(futures[future])

                if result.error is None:
                    done_count += 1
                    token_totals["prompt"] += result.prompt_tokens
                    token_totals["completion"] += result.completion_tokens
                    token_totals["reasoning"] += result.reasoning_tokens or 0
                    model_tokens[result.model]["prompt"] += result.prompt_tokens
                    model_tokens[result.model]["completion"] += result.completion_tokens
                    model_tokens[result.model]["reasoning"] += result.reasoning_tokens or 0
                    model_latencies[result.model].append(result.elapsed_seconds)
                    model_done[result.model] += 1
                    stat = {
                        "filename": result.filename,
                        "input_chars": result.input_chars,
                        "output_chars": result.output_chars,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "reasoning_tokens": result.reasoning_tokens,
                        "total_tokens": result.total_tokens,
                        "elapsed_seconds": result.elapsed_seconds,
                    }
                    run_stats[result.run_name].append(stat)

                    # Find output_dir for this model's run
                    run_output_dir = output_base / result.run_name
                    _append_run_stats(run_output_dir / "run_stats.csv", stat)

                    row: list[str] = [str(result.index)]
                    if multi_model:
                        row.append(result.run_name)
                    row.extend([
                        result.filename,
                        f"{result.input_chars:,}",
                        f"{result.output_chars:,}",
                        f"{result.reduction:.0f}%",
                        f"{result.elapsed_seconds:.1f}s",
                        f"{result.total_tokens:,}",
                    ])
                    table_rows.append(tuple(row))
                else:
                    error_count += 1
                    run_errors[result.run_name].append({
                        "filename": result.filename,
                        "error": result.error,
                        "elapsed_seconds": result.elapsed_seconds,
                    })

                    row = [str(result.index)]
                    if multi_model:
                        row.append(result.run_name)
                    row.extend([
                        result.filename,
                        f"{result.input_chars:,}",
                        "[red]ERROR[/red]",
                        "",
                        f"{result.elapsed_seconds:.1f}s",
                        "",
                    ])
                    table_rows.append(tuple(row))

                live.update(build_display())

    # Summary
    console.print()
    console.rule("[bold]Runs[/bold]")
    for rn in run_names_seen:
        n_ok = len(run_stats[rn])
        n_err = len(run_errors[rn])
        err_str = f", [red]{n_err} errors[/red]" if n_err else ""
        console.print(f"  {rn}: [green]{n_ok} done[/green]{err_str}  →  {output_base / rn / 'run_stats.csv'}")
        for err in run_errors[rn]:
            console.print(f"    [red]{err['filename']}[/red]: {err['error']}")

    console.rule("[bold]Token Usage by Model[/bold]")
    summary_table = Table(show_lines=False, pad_edge=False, box=None)
    summary_table.add_column("Model", style="bold", no_wrap=True)
    summary_table.add_column("Prompt", justify="right")
    summary_table.add_column("Completion", justify="right")
    summary_table.add_column("Reasoning", justify="right")
    summary_table.add_column("Total", justify="right", style="cyan")
    summary_table.add_column("Avg Latency", justify="right", style="dim")
    for m in model_tokens:
        mt = model_tokens[m]
        total_tok = mt["prompt"] + mt["completion"]
        lat = model_latencies[m]
        avg_lat = f"{statistics.mean(lat):.1f}s" if lat else "-"
        reasoning_str = f"{mt['reasoning']:,}" if mt["reasoning"] else "-"
        summary_table.add_row(
            m,
            f"{mt['prompt']:,}",
            f"{mt['completion']:,}",
            reasoning_str,
            f"{total_tok:,}",
            avg_lat,
        )
    console.print(summary_table)
