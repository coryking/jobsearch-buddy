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

from jobbuddy.eval.models import KNOWN_MODELS, ModelConfig
from jobbuddy.eval.utils import PROMPTS_DIR, pick_models, pick_prompt
from jobbuddy.settings import get_settings


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 → '1.2K', 12345 → '12.3K', 500 → '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


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
) -> None:
    """Run strip eval: prompt+model against samples."""
    if prompt is None:
        prompt = pick_prompt(PROMPTS_DIR)

    if not prompt.exists():
        print(f"Prompt file not found: {prompt}")
        raise typer.Exit(1)
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    if model is None:
        models = pick_models(prompt.stem, output)
    else:
        models = [model]

    sample_files = sorted(
        f for f in samples.glob("*.txt")
        if f.name != "sample_manifest.json"
    )
    if not sample_files:
        print(f"No .txt samples found in {samples}")
        raise typer.Exit(1)

    prompt_text = prompt.read_text(encoding="utf-8").strip()

    # Build work items: every (model, sample) pair, skipping already-done
    work_items: list[dict] = []
    skipped = 0
    for m in models:
        config = KNOWN_MODELS.get(m, ModelConfig())
        name = run_name if run_name else f"{prompt.stem}-{m}"
        output_dir = output / name
        output_dir.mkdir(parents=True, exist_ok=True)
        for i, sample_file in enumerate(sample_files, 1):
            if (output_dir / sample_file.name).exists():
                skipped += 1
                continue
            work_items.append({
                "model": m,
                "config": config,
                "run_name": name,
                "output_dir": output_dir,
                "sample_file": sample_file,
                "index": i,
            })

    if skipped:
        print(f"Skipping {skipped} already-completed samples")

    if not work_items:
        print("All samples already completed!")
        raise typer.Exit(0)

    _run_all(work_items, prompt, prompt_text, sample_files, models, output, workers)


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
    prompt_file: Path,
    prompt_text: str,
    sample_files: list[Path],
    models: list[str],
    output_base: Path,
    workers: int,
) -> None:
    console = Console()
    multi_model = len(models) > 1

    for m in models:
        config = KNOWN_MODELS.get(m, ModelConfig())
        deployment = config.resolve_deployment(m)
        params_str = ", ".join(f"{k}={v}" for k, v in config.api_params.items()) if config.api_params else "defaults"
        if deployment != m:
            params_str = f"deployment={deployment}, {params_str}"
        console.print(f"  {m} ({params_str})")
    console.print(f"{len(work_items)} total items ({len(sample_files)} samples x {len(models)} models), workers={workers}")

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
    # Per-model collectors for metadata
    model_stats: dict[str, list[dict]] = {m: [] for m in models}
    model_errors: dict[str, list[dict]] = {m: [] for m in models}
    table_rows: list[tuple[str, ...]] = []
    running_items: set[str] = set()
    token_totals = {"prompt": 0, "completion": 0, "reasoning": 0}

    def build_display() -> Group:
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
        tok_total = token_totals["prompt"] + token_totals["completion"]
        if tok_total:
            tok_parts = [f"prompt: {_fmt_tokens(token_totals['prompt'])}",
                         f"completion: {_fmt_tokens(token_totals['completion'])}"]
            if token_totals["reasoning"]:
                tok_parts.append(f"reasoning: {_fmt_tokens(token_totals['reasoning'])}")
            parts.append(f"[cyan]{' | '.join(tok_parts)}[/cyan]")
        status = Text.from_markup("  \u2502  ".join(parts))

        table = Table(show_lines=False, pad_edge=False)
        table.add_column("#", justify="right", style="dim", width=4)
        if multi_model:
            table.add_column("Model", style="cyan", no_wrap=True)
        table.add_column("File", style="bold", no_wrap=True)
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Reduc", justify="right")
        table.add_column("Time", justify="right")
        table.add_column("Tokens", justify="right")
        # status(1) + table header(2) + ellipsis(1) + margin(2) = 6 base
        overhead = 6
        max_rows = max(console.height - overhead, 5)
        visible = table_rows[-max_rows:]
        if len(table_rows) > max_rows:
            table.add_row(*["..."] * len(table.columns))
        for row in visible:
            table.add_row(*row)

        return Group(status, table)

    with Live(build_display(), console=console, refresh_per_second=4) as live:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for item in work_items:
                future = executor.submit(
                    _process_sample,
                    client, item["model"], item["config"], prompt_text,
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
                    model_stats[result.model].append(stat)

                    row: list[str] = [str(result.index)]
                    if multi_model:
                        row.append(result.model)
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
                    model_errors[result.model].append({
                        "filename": result.filename,
                        "error": result.error,
                        "elapsed_seconds": result.elapsed_seconds,
                    })

                    row = [str(result.index)]
                    if multi_model:
                        row.append(result.model)
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

    # Write per-model run_meta.json
    prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:12]
    timestamp = datetime.now(timezone.utc).isoformat()

    for m in models:
        stats = model_stats[m]
        errs = model_errors[m]
        run_name_m = f"{prompt_file.stem}-{m}"
        output_dir = output_base / run_name_m

        if stats:
            latencies = [s["elapsed_seconds"] for s in stats]
            aggregates = {
                "mean_latency": round(statistics.mean(latencies), 3),
                "median_latency": round(statistics.median(latencies), 3),
                "p95_latency": round(sorted(latencies)[int(len(latencies) * 0.95)], 3),
                "total_seconds": round(sum(latencies), 1),
                "total_tokens": sum(s["total_tokens"] for s in stats),
                "total_prompt_tokens": sum(s["prompt_tokens"] for s in stats),
                "total_completion_tokens": sum(s["completion_tokens"] for s in stats),
                "total_reasoning_tokens": sum(s["reasoning_tokens"] for s in stats),
            }
        else:
            aggregates = {}

        meta = {
            "run_name": run_name_m,
            "model": m,
            "model_params": KNOWN_MODELS.get(m, ModelConfig()).api_params,
            "prompt_file": str(prompt_file),
            "prompt_sha256": prompt_hash,
            "timestamp": timestamp,
            "sample_count": len(sample_files),
            "success_count": len(stats),
            "error_count": len(errs),
            "workers": workers,
            "aggregates": aggregates,
            "files": stats,
            "errors": errs,
        }

        meta_path = output_dir / "run_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Summary
    console.print()
    console.rule("[bold]Summary[/bold]")
    for m in models:
        stats = model_stats[m]
        errs = model_errors[m]
        console.print(f"\n  [bold]{m}[/bold]: [green]{len(stats)} succeeded[/green], [red]{len(errs)} failed[/red]")
        if stats:
            latencies = [s["elapsed_seconds"] for s in stats]
            prompt_tokens = sum(s["prompt_tokens"] for s in stats)
            completion_tokens = sum(s["completion_tokens"] for s in stats)
            reasoning_tokens = sum(s["reasoning_tokens"] for s in stats)
            console.print(f"    Latency: mean={statistics.mean(latencies):.3f}s, median={statistics.median(latencies):.3f}s")
            tok_parts = [f"prompt: {prompt_tokens:,}", f"completion: {completion_tokens:,}"]
            if reasoning_tokens:
                tok_parts.append(f"reasoning: {reasoning_tokens:,}")
            console.print(f"    Tokens: {' | '.join(tok_parts)} | total: {prompt_tokens + completion_tokens:,}")
        if errs:
            for err in errs:
                console.print(f"    [red]{err['filename']}[/red]: {err['error']}")
        console.print(f"    Metadata: {output_base / f'{prompt_file.stem}-{m}' / 'run_meta.json'}")
