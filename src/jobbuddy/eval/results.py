"""Display eval results for a prompt across all models.

Two subcommands:
  summary — JSON: scores, cost, perf per model
  notes   — JSON: judge reasoning for specific files/models
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Annotated, Optional

import typer

from jobbuddy.eval.judge import SCORE_FIELDS
from jobbuddy.eval.models import KNOWN_MODELS, ModelConfig
from jobbuddy.eval.utils import RUNS_DIR, pick_prompt, PROMPTS_DIR

SCORES_DIR = Path("eval/data/scores")

results_app = typer.Typer(help="Eval results: summary scores or per-file judge notes.")


# --- Shared helpers ---

def _load_prompt_runs(runs_dir: Path) -> dict[str, list[dict]]:
    """Map prompt stem → list of {run_name, model, run_dir} by parsing dir names.

    Convention: run dirs are named ``{prompt_stem}-{model}``.  We match against
    known prompt files (longest stem first) so ``v4-edges`` matches before ``v4``.
    """
    prompt_stems = sorted(
        (p.stem for p in PROMPTS_DIR.glob("*.txt") if p.stem != "judge"),
        key=len,
        reverse=True,
    )

    prompt_runs: dict[str, list[dict]] = {}
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        name = run_dir.name
        for stem in prompt_stems:
            prefix = f"{stem}-"
            if name.startswith(prefix) and len(name) > len(prefix):
                model = name[len(prefix):]
                prompt_runs.setdefault(stem, []).append({
                    "run_name": name,
                    "model": model,
                    "run_dir": run_dir,
                })
                break
    return prompt_runs


def _resolve_prompt(prompt: str | None, prompt_runs: dict) -> str:
    """Resolve prompt stem, using interactive picker if None."""
    if prompt is None:
        selected = pick_prompt(PROMPTS_DIR)
        return selected.stem
    return prompt


def _validate_prompt(prompt: str, prompt_runs: dict) -> None:
    """Exit with error if prompt has no runs."""
    if prompt not in prompt_runs:
        available = ", ".join(sorted(prompt_runs.keys()))
        print(f"No runs found for prompt '{prompt}'. Available: {available}")
        raise typer.Exit(1)


def _build_pivot(
    runs: list[dict],
    scores_file: Path,
) -> tuple[list[str], list[str], dict[str, dict[str, dict]], dict[str, str], dict[str, Path]]:
    """Load scores and build the pivot table.

    Returns (models, filenames, pivot, run_for_model, dir_by_run).
    """
    run_names = {r["run_name"] for r in runs}
    model_by_run = {r["run_name"]: r["model"] for r in runs}
    dir_by_run = {r["run_name"]: r["run_dir"] for r in runs}

    models = sorted(set(model_by_run.values()))
    run_for_model = {model_by_run[rn]: rn for rn in run_names}

    all_scores = _load_judge_scores(scores_file)
    filtered = [row for row in all_scores if row["run_name"] in run_names]

    if not filtered:
        return models, [], {}, run_for_model, dir_by_run

    pivot: dict[str, dict[str, dict]] = {}
    for row in filtered:
        fn = row["filename"]
        model = model_by_run.get(row["run_name"], row["run_name"])
        entry = {"reasoning": row.get("reasoning", "")}
        for f in SCORE_FIELDS:
            entry[f] = row.get(f, "")
        pivot.setdefault(fn, {})[model] = entry

    filenames = sorted(pivot.keys())
    return models, filenames, pivot, run_for_model, dir_by_run


def _load_run_stats(run_dir: Path) -> list[dict]:
    """Read run_stats.csv from a run directory, returning list of row dicts."""
    csv_path = run_dir / "run_stats.csv"
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_judge_scores(scores_path: Path) -> list[dict]:
    """Load judge_scores.csv rows."""
    if not scores_path.exists():
        return []
    with open(scores_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_model_data(
    m: str,
    filenames: list[str],
    pivot: dict[str, dict[str, dict]],
    run_for_model: dict[str, str],
    dir_by_run: dict[str, Path],
) -> dict:
    """Build the JSON data for a single model."""
    rn = run_for_model[m]
    run_dir = dir_by_run[rn]

    # Per-file scores and dimension accumulators
    scores = {}
    dim_accum: dict[str, list[float]] = {f: [] for f in SCORE_FIELDS}
    for fn in filenames:
        entry = pivot[fn].get(m)
        if entry and entry.get(SCORE_FIELDS[0]):
            file_scores = {}
            for f in SCORE_FIELDS:
                v = entry.get(f, "")
                try:
                    fv = float(v)
                    file_scores[f] = int(fv) if fv == int(fv) else fv
                    dim_accum[f].append(fv)
                except (ValueError, TypeError):
                    file_scores[f] = None
            scores[fn] = file_scores

    # Mean scores
    mean = {}
    for f in SCORE_FIELDS:
        vals = dim_accum[f]
        mean[f] = round(statistics.mean(vals), 1) if vals else None

    # Cost / perf from run_stats.csv
    rows = _load_run_stats(run_dir)
    model_data: dict = {
        "run_dir": str(run_dir),
        "mean": mean,
        "scores": scores,
    }

    if rows:
        n = len(rows)
        prompt_tok = sum(int(r["prompt_tokens"]) for r in rows)
        compl_tok_total = sum(int(r["completion_tokens"]) for r in rows)
        reason_tok_total = sum(int(r.get("reasoning_tokens", 0) or 0) for r in rows)
        total_tok = sum(int(r["total_tokens"]) for r in rows)

        latencies = [float(r["elapsed_seconds"]) for r in rows]
        input_per_sample = [int(r["prompt_tokens"]) for r in rows]
        visible_per_sample = [
            int(r["total_tokens"]) - int(r["prompt_tokens"])
            - int(r.get("reasoning_tokens", 0) or 0)
            for r in rows
        ]
        visible_tps = [
            vis / float(r["elapsed_seconds"])
            for r, vis in zip(rows, visible_per_sample)
            if float(r["elapsed_seconds"]) > 0
        ]

        def _stat_triple(vals: list[float]) -> dict | None:
            if not vals:
                return None
            return {
                "mean": round(statistics.mean(vals), 1),
                "median": round(statistics.median(vals), 1),
                "stdev": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
            }

        cfg = KNOWN_MODELS.get(m, ModelConfig())
        billable_output = total_tok - prompt_tok
        cost_usd = cfg.cost(prompt_tok, billable_output)

        model_data["cost"] = {
            "usd": round(cost_usd, 4) if cost_usd is not None else None,
            "input_tokens": prompt_tok,
            "output_tokens": compl_tok_total,
            "reasoning_tokens": reason_tok_total if reason_tok_total else None,
        }
        model_data["perf"] = {
            "n": n,
            "rpm": cfg.rpm if cfg.rpm else None,
            "latency": _stat_triple(latencies),
            "visible_tok_per_sec": _stat_triple(visible_tps),
        }

    return model_data


# --- Subcommands ---

@results_app.command()
def summary(
    prompt: Annotated[
        Optional[str],
        typer.Argument(help="Prompt stem (e.g. 'v4-edges'). Interactive picker if omitted."),
    ] = None,
    model: Annotated[
        Optional[list[str]],
        typer.Option(help="Filter to specific model(s). Repeat for multiple: --model gpt-5-nano --model gpt-5-mini"),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Runs directory")] = RUNS_DIR,
    scores_file: Annotated[
        Path, typer.Option(help="Judge scores CSV")
    ] = SCORES_DIR / "judge_scores.csv",
) -> None:
    """Score matrix, cost, and perf for a prompt across all models (JSON)."""
    prompt_runs = _load_prompt_runs(runs_dir)
    prompt = _resolve_prompt(prompt, prompt_runs)
    _validate_prompt(prompt, prompt_runs)

    runs = prompt_runs[prompt]
    models_all, filenames, pivot, run_for_model, dir_by_run = _build_pivot(runs, scores_file)

    # Filter models if requested
    if model:
        missing = [m for m in model if m not in models_all]
        if missing:
            print(f"Model(s) not found: {', '.join(missing)}. Available: {', '.join(models_all)}")
            raise typer.Exit(1)
        models = [m for m in models_all if m in model]
    else:
        models = models_all

    if not filenames:
        print(f"No judge scores found for prompt '{prompt}'.")
        print(f"Run: jsb-eval judge --run {runs_dir}/<run-name>/")
        raise typer.Exit(1)

    result = {
        "prompt": prompt,
        "strip_prompt": str(PROMPTS_DIR / f"{prompt}.txt"),
        "judge_prompt": str(PROMPTS_DIR / "judge.txt"),
        "scores_file": str(scores_file),
        "dimensions": list(SCORE_FIELDS),
        "models": {},
    }

    for m in models:
        result["models"][m] = _build_model_data(
            m, filenames, pivot, run_for_model, dir_by_run,
        )

    print(json.dumps(result, indent=2))


@results_app.command()
def notes(
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt stem (e.g. 'v4-edges')."),
    ],
    filename: Annotated[
        list[str],
        typer.Argument(help="Filename substring(s) to show notes for."),
    ],
    model: Annotated[
        Optional[list[str]],
        typer.Option(help="Filter to specific model(s). Repeat for multiple."),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Runs directory")] = RUNS_DIR,
    scores_file: Annotated[
        Path, typer.Option(help="Judge scores CSV")
    ] = SCORES_DIR / "judge_scores.csv",
) -> None:
    """Judge reasoning for specific files (JSON)."""
    prompt_runs = _load_prompt_runs(runs_dir)
    _validate_prompt(prompt, prompt_runs)

    runs = prompt_runs[prompt]
    models_all, filenames, pivot, run_for_model, dir_by_run = _build_pivot(runs, scores_file)

    if not filenames:
        print(f"No judge scores found for prompt '{prompt}'.")
        raise typer.Exit(1)

    # Filter models
    if model:
        missing = [m for m in model if m not in models_all]
        if missing:
            print(f"Model(s) not found: {', '.join(missing)}. Available: {', '.join(models_all)}")
            raise typer.Exit(1)
        models = [m for m in models_all if m in model]
    else:
        models = models_all

    # Filter filenames by substring match
    filters = [f.lower() for f in filename]
    matched = [
        fn for fn in filenames
        if any(filt in fn.lower() for filt in filters)
    ]

    if not matched:
        print(f"No files matched: {', '.join(filename)}")
        print(f"Available: {', '.join(filenames)}")
        raise typer.Exit(1)

    samples_dir = Path("eval/data/samples")

    result: dict = {
        "prompt": prompt,
        "files": {},
    }

    for fn in matched:
        file_data: dict = {
            "original": str(samples_dir / fn),
            "models": {},
        }
        for m in models:
            rn = run_for_model.get(m, "")
            model_entry: dict = {
                "stripped": str(runs_dir / rn / fn),
            }
            entry = pivot[fn].get(m)
            if entry:
                scores = {}
                for f in SCORE_FIELDS:
                    v = entry.get(f, "")
                    try:
                        fv = float(v)
                        scores[f] = int(fv) if fv == int(fv) else fv
                    except (ValueError, TypeError):
                        scores[f] = None
                model_entry["scores"] = scores
                model_entry["reasoning"] = entry.get("reasoning", "")
            file_data["models"][m] = model_entry
        result["files"][fn] = file_data

    print(json.dumps(result, indent=2))
