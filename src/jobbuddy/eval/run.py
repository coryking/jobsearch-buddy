"""Run a prompt+model combination against all samples.

Self-contained LLM client -- reads Azure credentials from settings,
calls the API, measures timing, writes stripped output + run_meta.json.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from openai import AzureOpenAI
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from jobbuddy.eval.utils import KNOWN_MODELS, PROMPTS_DIR, pick_models, pick_prompt
from jobbuddy.settings import get_settings


@dataclass
class _SampleResult:
    """Result from processing one sample file."""
    index: int
    filename: str
    input_chars: int
    output_chars: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
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
    workers: Annotated[int, typer.Option(help="Concurrent API workers")] = 5,
) -> None:
    """Run strip eval: prompt+model against samples."""
    # Pick prompt first (need stem to check which models already ran)
    if prompt is None:
        prompt = pick_prompt(PROMPTS_DIR)

    if not prompt.exists():
        print(f"Prompt file not found: {prompt}")
        raise typer.Exit(1)
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    # Pick model(s)
    if model is None:
        models = pick_models(prompt.stem, output)
    else:
        models = [model]

    for m in models:
        name = run_name if run_name else f"{prompt.stem}-{m}"
        _run_eval(prompt, m, samples, name, output, workers)


def _process_sample(
    client: AzureOpenAI,
    model: str,
    model_params: dict,
    prompt_text: str,
    sample_file: Path,
    output_dir: Path,
    index: int,
    running_files: set[str],
) -> _SampleResult:
    """Process a single sample file. Runs in a worker thread."""
    running_files.add(sample_file.name)
    description = sample_file.read_text(encoding="utf-8")
    input_chars = len(description)

    try:
        start = time.monotonic()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": description},
            ],
            **model_params,
        )
        elapsed = time.monotonic() - start

        result_text = response.choices[0].message.content.strip()
        usage = response.usage

        out_file = output_dir / sample_file.name
        out_file.write_text(result_text, encoding="utf-8")

        reduction = ((input_chars - len(result_text)) / input_chars * 100) if input_chars else 0

        return _SampleResult(
            index=index,
            filename=sample_file.name,
            input_chars=input_chars,
            output_chars=len(result_text),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            elapsed_seconds=round(elapsed, 3),
            reduction=reduction,
            error=None,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        return _SampleResult(
            index=index,
            filename=sample_file.name,
            input_chars=input_chars,
            output_chars=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            elapsed_seconds=round(elapsed, 3),
            reduction=None,
            error=str(e),
        )


def _run_eval(
    prompt_file: Path,
    model: str,
    samples_dir: Path,
    run_name: str,
    output_base: Path,
    workers: int,
) -> None:
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    output_dir = output_base / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_files = sorted(
        f for f in samples_dir.glob("*.txt")
        if f.name != "sample_manifest.json"
    )
    if not sample_files:
        print(f"No .txt samples found in {samples_dir}")
        return

    model_params = KNOWN_MODELS.get(model, {})
    console = Console()
    params_str = ", ".join(f"{k}={v}" for k, v in model_params.items()) if model_params else "defaults"
    console.print(f"Running {len(sample_files)} samples with model={model} ({params_str}), prompt={prompt_file.name}, workers={workers}")
    console.print(f"Output: {output_dir}/")

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=60.0,
    )

    total = len(sample_files)
    done_count = 0
    error_count = 0
    file_stats: list[dict] = []
    errors: list[dict] = []
    table_rows: list[tuple[str, ...]] = []

    # Track which samples are currently being processed by workers
    running_files: set[str] = set()

    def build_display() -> Group:
        queued = total - done_count - error_count - len(running_files)
        parts = []
        if running_files:
            parts.append(f"[yellow bold]\u23f3 {len(running_files)} running[/yellow bold]")
        if done_count:
            parts.append(f"[green]\u2713 {done_count} done[/green]")
        if error_count:
            parts.append(f"[red]\u2717 {error_count} errors[/red]")
        if queued > 0:
            parts.append(f"[dim]\u00b7 {queued} queued[/dim]")
        status = Text.from_markup("  \u2502  ".join(parts))

        table = Table(show_lines=False, pad_edge=False)
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("File", style="bold", no_wrap=True)
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Reduc", justify="right")
        table.add_column("Time", justify="right")
        table.add_column("Tokens", justify="right")
        for row in table_rows:
            table.add_row(*row)

        return Group(status, table)

    with Live(build_display(), console=console, refresh_per_second=4) as live:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for i, sample_file in enumerate(sample_files, 1):
                future = executor.submit(
                    _process_sample,
                    client, model, model_params, prompt_text,
                    sample_file, output_dir, i, running_files,
                )
                futures[future] = sample_file.name

            for future in as_completed(futures):
                result = future.result()
                running_files.discard(futures[future])

                if result.error is None:
                    done_count += 1
                    file_stats.append({
                        "filename": result.filename,
                        "input_chars": result.input_chars,
                        "output_chars": result.output_chars,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "total_tokens": result.total_tokens,
                        "elapsed_seconds": result.elapsed_seconds,
                    })
                    table_rows.append((
                        str(result.index),
                        result.filename,
                        f"{result.input_chars:,}",
                        f"{result.output_chars:,}",
                        f"{result.reduction:.0f}%",
                        f"{result.elapsed_seconds:.1f}s",
                        f"{result.total_tokens:,}",
                    ))
                else:
                    error_count += 1
                    errors.append({
                        "filename": result.filename,
                        "error": result.error,
                        "elapsed_seconds": result.elapsed_seconds,
                    })
                    table_rows.append((
                        str(result.index),
                        result.filename,
                        f"{result.input_chars:,}",
                        "[red]ERROR[/red]",
                        "",
                        f"{result.elapsed_seconds:.1f}s",
                        "",
                    ))

                live.update(build_display())

    # Aggregate stats
    if file_stats:
        latencies = [s["elapsed_seconds"] for s in file_stats]
        total_tokens = sum(s["total_tokens"] for s in file_stats)
        prompt_tokens = sum(s["prompt_tokens"] for s in file_stats)
        completion_tokens = sum(s["completion_tokens"] for s in file_stats)

        aggregates = {
            "mean_latency": round(statistics.mean(latencies), 3),
            "median_latency": round(statistics.median(latencies), 3),
            "p95_latency": round(sorted(latencies)[int(len(latencies) * 0.95)], 3),
            "total_seconds": round(sum(latencies), 1),
            "total_tokens": total_tokens,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
        }
    else:
        aggregates = {}

    meta = {
        "run_name": run_name,
        "model": model,
        "model_params": model_params,
        "prompt_file": str(prompt_file),
        "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest()[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_count": total,
        "success_count": len(file_stats),
        "error_count": len(errors),
        "workers": workers,
        "aggregates": aggregates,
        "files": file_stats,
        "errors": errors,
    }

    meta_path = output_dir / "run_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    console.print()
    console.rule("[bold]Summary[/bold]")
    console.print(f"  [green]{len(file_stats)} succeeded[/green], [red]{len(errors)} failed[/red]")
    if aggregates:
        console.print(f"  Latency: mean={aggregates['mean_latency']}s, median={aggregates['median_latency']}s, p95={aggregates['p95_latency']}s")
        console.print(f"  Tokens: {aggregates['total_tokens']:,} total ({aggregates['total_prompt_tokens']:,} prompt + {aggregates['total_completion_tokens']:,} completion)")
        console.print(f"  Total time: {aggregates['total_seconds']}s")
    if errors:
        console.print()
        console.rule("[bold red]Errors[/bold red]")
        for err in errors:
            console.print(f"  [bold]{err['filename']}[/bold]: {err['error']}")
    console.print(f"  Metadata: {meta_path}")
