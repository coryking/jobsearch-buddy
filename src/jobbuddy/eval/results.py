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

from jobbuddy.eval.judge import SCORE_FIELDS
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

    # Pivot: filename → {model: {recall, precision, integrity, fidelity, reasoning}}
    pivot: dict[str, dict[str, dict]] = {}
    for row in filtered:
        fn = row["filename"]
        model = model_by_run.get(row["run_name"], row["run_name"])
        entry = {"reasoning": row.get("reasoning", "")}
        for f in SCORE_FIELDS:
            entry[f] = row.get(f, "")
        pivot.setdefault(fn, {})[model] = entry

    filenames = sorted(pivot.keys())

    # --- Header with paths ---
    prompt_file = runs[0].get("prompt_file", f"eval/prompts/{prompt}.txt")
    lines = [
        f"## Eval Results: {prompt}",
        f"prompt: {prompt_file}",
        f"scores: {scores_file}",
        f"runs:   {runs_dir}/{{{'|'.join(run_for_model[m] for m in models)}}}",
        f"dimensions: R=recall P=precision I=integrity F=fidelity",
        "",
    ]

    # --- Score matrix as CSV (R/P/I/F compact format) ---
    header = "filename," + ",".join(models)
    lines.append(header)

    # Accumulate per-dimension scores for means
    model_dim_scores: dict[str, dict[str, list[float]]] = {
        m: {f: [] for f in SCORE_FIELDS} for m in models
    }
    for fn in filenames:
        short = fn[:60] + "…" if len(fn) > 60 else fn
        cells = []
        for m in models:
            entry = pivot[fn].get(m)
            if entry and entry.get(SCORE_FIELDS[0]):
                dim_vals = []
                for f in SCORE_FIELDS:
                    v = entry.get(f, "")
                    dim_vals.append(str(v))
                    try:
                        model_dim_scores[m][f].append(float(v))
                    except (ValueError, TypeError):
                        pass
                cells.append("/".join(dim_vals))
            else:
                cells.append("-")
        lines.append(f"{short},{','.join(cells)}")

    # Mean row per dimension
    mean_cells = []
    for m in models:
        dim_means = []
        for f in SCORE_FIELDS:
            vals = model_dim_scores[m][f]
            if vals:
                dim_means.append(f"{statistics.mean(vals):.1f}")
            else:
                dim_means.append("-")
        mean_cells.append("/".join(dim_means))
    lines.append(f"MEAN,{','.join(mean_cells)}")

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
                dim_strs = []
                for f in SCORE_FIELDS:
                    v = entry.get(f, "?")
                    dim_strs.append(f"{f[0].upper()}{v}")
                score_str = "/".join(dim_strs)
                reasoning = entry["reasoning"] or "(no reasoning)"
                lines.append(f"- {m} ({score_str}): {reasoning}")
            else:
                lines.append(f"- {m}: (not scored)")
        lines.append("")

    print("\n".join(lines))
