"""Display eval results for a prompt across all models.

Reads judge_scores.csv, groups by prompt, pivots to a per-model score matrix,
and prints reasoning for each file. Output is plain text optimized for LLM
consumption (CSV-style tables, not Rich formatting).
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Annotated, Optional

import typer

from jobbuddy.eval.utils import RUNS_DIR, pick_prompt, PROMPTS_DIR

SCORES_DIR = Path("eval/data/scores")


def _load_prompt_runs(runs_dir: Path) -> dict[str, list[dict]]:
    """Map prompt_file stem → list of run_meta dicts."""
    prompt_runs: dict[str, list[dict]] = {}
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "run_meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stem = Path(meta.get("prompt_file", "")).stem
        prompt_runs.setdefault(stem, []).append(meta)
    return prompt_runs


def _load_judge_scores(scores_path: Path) -> list[dict]:
    """Load judge_scores.csv rows."""
    if not scores_path.exists():
        return []
    with open(scores_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def results(
    prompt: Annotated[
        Optional[str],
        typer.Argument(help="Prompt stem (e.g. 'v3-why'). Interactive picker if omitted."),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Runs directory")] = RUNS_DIR,
    scores_file: Annotated[
        Path, typer.Option(help="Judge scores CSV")
    ] = SCORES_DIR / "judge_scores.csv",
) -> None:
    """Show eval results for a prompt across all models."""
    prompt_runs = _load_prompt_runs(runs_dir)

    if prompt is None:
        selected = pick_prompt(PROMPTS_DIR)
        prompt = selected.stem

    if prompt not in prompt_runs:
        available = ", ".join(sorted(prompt_runs.keys()))
        print(f"No runs found for prompt '{prompt}'. Available: {available}")
        raise typer.Exit(1)

    runs = prompt_runs[prompt]
    run_names = {r["run_name"] for r in runs}
    model_by_run = {r["run_name"]: r.get("model", r["run_name"]) for r in runs}

    # Sort models for consistent column order
    models = sorted(set(model_by_run.values()))
    run_for_model = {model_by_run[rn]: rn for rn in run_names}

    # Load and filter scores
    all_scores = _load_judge_scores(scores_file)
    filtered = [row for row in all_scores if row["run_name"] in run_names]

    if not filtered:
        print(f"No judge scores found for prompt '{prompt}'.")
        print(f"Run: ats-eval judge --run {runs_dir}/<run-name>/")
        raise typer.Exit(1)

    # Pivot: filename → {model: {score, reasoning}}
    pivot: dict[str, dict[str, dict]] = {}
    for row in filtered:
        fn = row["filename"]
        model = model_by_run.get(row["run_name"], row["run_name"])
        pivot.setdefault(fn, {})[model] = {
            "score": row.get("score", ""),
            "reasoning": row.get("reasoning", ""),
        }

    filenames = sorted(pivot.keys())

    # --- Header with paths ---
    prompt_file = runs[0].get("prompt_file", f"eval/prompts/{prompt}.txt")
    lines = [
        f"## Eval Results: {prompt}",
        f"prompt: {prompt_file}",
        f"scores: {scores_file}",
        f"runs:   {runs_dir}/{{{'|'.join(run_for_model[m] for m in models)}}}",
        "",
    ]

    # --- Score matrix as CSV ---
    header = "filename," + ",".join(models)
    lines.append(header)

    model_scores: dict[str, list[float]] = {m: [] for m in models}
    for fn in filenames:
        short = fn[:60] + "…" if len(fn) > 60 else fn
        cells = []
        for m in models:
            entry = pivot[fn].get(m)
            if entry and entry["score"]:
                score = entry["score"]
                cells.append(str(score))
                try:
                    model_scores[m].append(float(score))
                except ValueError:
                    pass
            else:
                cells.append("-")
        lines.append(f"{short},{','.join(cells)}")

    # Mean row
    mean_cells = []
    for m in models:
        if model_scores[m]:
            mean_cells.append(f"{statistics.mean(model_scores[m]):.2f}")
        else:
            mean_cells.append("-")
    lines.append(f"MEAN,{','.join(mean_cells)}")

    # Median row
    median_cells = []
    for m in models:
        if model_scores[m]:
            median_cells.append(f"{statistics.median(model_scores[m]):.1f}")
        else:
            median_cells.append("-")
    lines.append(f"MEDIAN,{','.join(median_cells)}")

    lines.append("")

    # --- Reasoning per file ---
    lines.append("## Reasoning")
    lines.append("")
    for fn in filenames:
        short = fn[:60] + "…" if len(fn) > 60 else fn
        lines.append(f"### {short}")
        for m in models:
            entry = pivot[fn].get(m)
            if entry:
                score = entry["score"] or "?"
                reasoning = entry["reasoning"] or "(no reasoning)"
                lines.append(f"- {m} ({score}): {reasoning}")
            else:
                lines.append(f"- {m}: (not scored)")
        lines.append("")

    print("\n".join(lines))
