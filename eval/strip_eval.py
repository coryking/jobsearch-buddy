#!/usr/bin/env python3
"""Run a prompt+model combination against all samples.

Self-contained LLM client — reads Azure credentials from settings,
calls the API, measures timing, writes stripped output + run_meta.json.

Usage:
    uv run python eval/strip_eval.py \
        --prompt eval/prompts/v1-original.txt \
        --model gpt-4.1-nano \
        --samples eval/data/samples/ \
        --run-name v1-gpt4.1nano
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import AzureOpenAI
from rich.console import Console
from rich.live import Live
from rich.table import Table

from jobbuddy.settings import get_settings


def run_eval(
    prompt_file: Path,
    model: str,
    samples_dir: Path,
    run_name: str,
    output_base: Path,
) -> None:
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    output_dir = output_base / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get sample files (skip manifest)
    sample_files = sorted(
        f for f in samples_dir.glob("*.txt")
        if f.name != "sample_manifest.json"
    )
    if not sample_files:
        print(f"No .txt samples found in {samples_dir}")
        return

    console = Console()
    console.print(f"Running {len(sample_files)} samples with model={model}, prompt={prompt_file.name}")
    console.print(f"Output: {output_dir}/")

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=60.0,
    )

    file_stats: list[dict] = []
    errors: list[dict] = []

    def build_table() -> Table:
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
        return table

    table_rows: list[tuple[str, ...]] = []

    with Live(build_table(), console=console, refresh_per_second=4) as live:
        for i, sample_file in enumerate(sample_files, 1):
            description = sample_file.read_text(encoding="utf-8")

            try:
                start = time.monotonic()
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": description},
                    ],
                )
                elapsed = time.monotonic() - start

                result = response.choices[0].message.content.strip()
                usage = response.usage

                # Write stripped output
                out_file = output_dir / sample_file.name
                out_file.write_text(result, encoding="utf-8")

                reduction = ((len(description) - len(result)) / len(description) * 100) if len(description) else 0

                stat = {
                    "filename": sample_file.name,
                    "input_chars": len(description),
                    "output_chars": len(result),
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "elapsed_seconds": round(elapsed, 3),
                }
                file_stats.append(stat)

                style = "red" if reduction < 5 else ""
                table_rows.append((
                    str(i),
                    sample_file.name,
                    f"{len(description):,}",
                    f"{len(result):,}",
                    f"{reduction:.0f}%",
                    f"{elapsed:.1f}s",
                    f"{usage.total_tokens:,}",
                ))

            except Exception as e:
                elapsed = time.monotonic() - start
                errors.append({
                    "filename": sample_file.name,
                    "error": str(e),
                    "elapsed_seconds": round(elapsed, 3),
                })
                table_rows.append((
                    str(i),
                    sample_file.name,
                    f"{len(description):,}",
                    "[red]ERROR[/red]",
                    "",
                    f"{elapsed:.1f}s",
                    str(e)[:40],
                ))

            live.update(build_table())

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

    # Write run metadata
    meta = {
        "run_name": run_name,
        "model": model,
        "prompt_file": str(prompt_file),
        "prompt_sha256": __import__("hashlib").sha256(prompt.encode()).hexdigest()[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(sample_files),
        "success_count": len(file_stats),
        "error_count": len(errors),
        "aggregates": aggregates,
        "files": file_stats,
        "errors": errors,
    }

    meta_path = output_dir / "run_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Summary
    console.print()
    console.rule("[bold]Summary[/bold]")
    console.print(f"  [green]{len(file_stats)} succeeded[/green], [red]{len(errors)} failed[/red]")
    if aggregates:
        console.print(f"  Latency: mean={aggregates['mean_latency']}s, median={aggregates['median_latency']}s, p95={aggregates['p95_latency']}s")
        console.print(f"  Tokens: {aggregates['total_tokens']:,} total ({aggregates['total_prompt_tokens']:,} prompt + {aggregates['total_completion_tokens']:,} completion)")
        console.print(f"  Total time: {aggregates['total_seconds']}s")
    console.print(f"  Metadata: {meta_path}")


def main():
    parser = argparse.ArgumentParser(description="Run strip eval: prompt+model against samples")
    parser.add_argument("--prompt", type=Path, required=True, help="Path to prompt text file")
    parser.add_argument("--model", type=str, required=True, help="Azure OpenAI model deployment name")
    parser.add_argument("--samples", type=Path, default=Path("eval/data/samples"), help="Samples directory")
    parser.add_argument("--run-name", type=str, required=True, help="Name for this run (becomes output subdir)")
    parser.add_argument("--output", type=Path, default=Path("eval/data/runs"), help="Base output directory for runs")
    args = parser.parse_args()

    if not args.prompt.exists():
        parser.error(f"Prompt file not found: {args.prompt}")
    if not args.samples.exists():
        parser.error(f"Samples directory not found: {args.samples}")

    run_eval(args.prompt, args.model, args.samples, args.run_name, args.output)


if __name__ == "__main__":
    main()
