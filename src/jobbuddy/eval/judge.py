"""LLM-as-judge -- auto-scores strip eval outputs.

For each sample, sends original + stripped to the judge model and
parses structured JSON scores.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from openai import AzureOpenAI

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


def judge(
    run: Annotated[Optional[Path], typer.Option(help="Path to run output directory")] = None,
    samples: Annotated[Path, typer.Option(help="Path to original samples directory")] = Path("eval/data/samples"),
    scores: Annotated[Path, typer.Option(help="Path to scores CSV output")] = Path("eval/data/scores/judge_scores.csv"),
    model: Annotated[str, typer.Option(help="Judge model deployment name")] = "gpt-5-mini",
    judge_prompt: Annotated[Optional[Path], typer.Option(help="Path to judge prompt")] = None,
) -> None:
    """LLM-as-judge auto-scoring of strip eval runs."""
    if run is None:
        from jobbuddy.eval.utils import pick_run
        run = pick_run()
    if not run.exists():
        print(f"Run directory not found: {run}")
        raise typer.Exit(1)
    if not samples.exists():
        print(f"Samples directory not found: {samples}")
        raise typer.Exit(1)

    prompt_file = judge_prompt or Path("eval/prompts/judge.txt")
    if not prompt_file.exists():
        print(f"Judge prompt not found: {prompt_file}")
        raise typer.Exit(1)

    run_name = run.name
    prompt_text = _load_judge_prompt(prompt_file)

    run_files = sorted(f for f in run.glob("*.txt"))
    if not run_files:
        print(f"No .txt files found in {run}")
        return

    # Check for already-judged
    already_judged: set[str] = set()
    if scores.exists():
        with scores.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["run_name"] == run_name:
                    already_judged.add(row["filename"])

    remaining = [f for f in run_files if f.name not in already_judged]
    if len(remaining) < len(run_files):
        print(f"Skipping {len(run_files) - len(remaining)} already-judged files")

    if not remaining:
        print("All files already judged!")
        return

    print(f"Judging {len(remaining)} files for run '{run_name}' with model={model}")

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=60.0,
    )

    write_header = not scores.exists()
    scores.parent.mkdir(parents=True, exist_ok=True)

    results = []
    errors = []

    for i, run_file in enumerate(remaining, 1):
        filename = run_file.name
        original_path = samples / filename

        if not original_path.exists():
            print(f"  [{i}/{len(remaining)}] {filename}: SKIP (original not found)")
            continue

        original = original_path.read_text(encoding="utf-8")
        stripped = run_file.read_text(encoding="utf-8")

        print(f"  [{i}/{len(remaining)}] {filename}...", end=" ", flush=True)

        try:
            start = time.monotonic()
            user_content = f"ORIGINAL:\n{original}\n\n---\n\nSTRIPPED:\n{stripped}"
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": user_content},
                ],
            )
            elapsed = time.monotonic() - start

            raw = response.choices[0].message.content.strip()
            parsed = _parse_judge_response(raw)

            if parsed is None:
                print(f"PARSE ERROR ({elapsed:.1f}s)")
                print(f"    Raw: {raw[:200]}")
                errors.append({"filename": filename, "error": "parse_error", "raw": raw[:500]})
                continue

            row = {
                "filename": filename,
                "run_name": run_name,
                "score": parsed["score"],
                "reasoning": parsed.get("reasoning", ""),
            }
            results.append(row)

            print(f"score={parsed['score']} {elapsed:.1f}s")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"filename": filename, "error": str(e)})

    # Write results
    if results:
        with scores.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
            if write_header:
                writer.writeheader()
            for row in results:
                writer.writerow(row)

    print(f"\nJudged: {len(results)} succeeded, {len(errors)} failed")
    if results:
        vals = [r["score"] for r in results]
        avg = sum(vals) / len(vals)
        print(f"  mean score: {avg:.2f}")
    print(f"Scores saved to: {scores}")
