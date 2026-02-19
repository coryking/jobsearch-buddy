"""Display eval results for a prompt across all models.

Two subcommands:
  summary — score matrix + cost table (compact, LLM-friendly)
  notes   — judge reasoning for specific files/models
"""

from __future__ import annotations

import csv
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


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 → '1.2K', 12345 → '12.3K', 500 → '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


# --- Subcommands ---

@results_app.command()
def summary(
    prompt: Annotated[
        Optional[str],
        typer.Argument(help="Prompt stem (e.g. 'v4-edges'). Interactive picker if omitted."),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Runs directory")] = RUNS_DIR,
    scores_file: Annotated[
        Path, typer.Option(help="Judge scores CSV")
    ] = SCORES_DIR / "judge_scores.csv",
) -> None:
    """Score matrix and cost table for a prompt across all models."""
    prompt_runs = _load_prompt_runs(runs_dir)
    prompt = _resolve_prompt(prompt, prompt_runs)
    _validate_prompt(prompt, prompt_runs)

    runs = prompt_runs[prompt]
    models, filenames, pivot, run_for_model, dir_by_run = _build_pivot(runs, scores_file)

    if not filenames:
        print(f"No judge scores found for prompt '{prompt}'.")
        print(f"Run: ats-eval judge --run {runs_dir}/<run-name>/")
        raise typer.Exit(1)

    # --- Header ---
    lines = [
        f"## Eval Results: {prompt}",
        f"strip prompt: {PROMPTS_DIR / f'{prompt}.txt'}",
        f"judge prompt: {PROMPTS_DIR / 'judge.txt'}",
        f"scores: {scores_file}",
        f"runs:   {runs_dir}/{{{'|'.join(run_for_model[m] for m in models)}}}",
        f"dimensions: R=recall P=precision I=integrity F=fidelity",
        "",
    ]

    # --- Score matrix ---
    header = "filename," + ",".join(models)
    lines.append(header)

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

    # Mean row
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

    # --- Cost / Performance ---
    model_stats: dict[str, list[dict]] = {}
    for m in models:
        rn = run_for_model[m]
        model_stats[m] = _load_run_stats(dir_by_run[rn])

    has_any_stats = any(model_stats[m] for m in models)
    if has_any_stats:
        lines.append("## Cost / Performance")
        lines.append("")
        cols = ["model", "samples", "prompt_tok", "compl_tok"]
        has_reasoning = any(
            any(int(r.get("reasoning_tokens", 0) or 0) for r in rows)
            for rows in model_stats.values()
        )
        if has_reasoning:
            cols.append("reason_tok")
        cols.extend(["total_tok", "cost", "mean_latency", "total_latency"])
        lines.append(",".join(cols))

        for m in models:
            rows = model_stats[m]
            if not rows:
                vals = [m] + ["-"] * (len(cols) - 1)
                lines.append(",".join(vals))
                continue
            n = len(rows)
            prompt_tok = sum(int(r["prompt_tokens"]) for r in rows)
            compl_tok = sum(int(r["completion_tokens"]) for r in rows)
            reason_tok = sum(int(r.get("reasoning_tokens", 0) or 0) for r in rows)
            total_tok = sum(int(r["total_tokens"]) for r in rows)
            latencies = [float(r["elapsed_seconds"]) for r in rows]
            mean_lat = statistics.mean(latencies)
            total_lat = sum(latencies)

            cfg = KNOWN_MODELS.get(m, ModelConfig())
            billable_output = total_tok - prompt_tok
            cost = cfg.cost(prompt_tok, billable_output)

            vals = [
                m,
                str(n),
                _fmt_tokens(prompt_tok),
                _fmt_tokens(compl_tok),
            ]
            if has_reasoning:
                vals.append(_fmt_tokens(reason_tok) if reason_tok else "0")
            vals.extend([
                _fmt_tokens(total_tok),
                f"${cost:.4f}" if cost is not None else "-",
                f"{mean_lat:.1f}s",
                f"{total_lat:.0f}s",
            ])
            lines.append(",".join(vals))

        lines.append("")

    lines.append("Use `ats-eval results notes <prompt> <filename>` for judge reasoning.")

    print("\n".join(lines))


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
        Optional[str],
        typer.Option(help="Filter to a single model."),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Runs directory")] = RUNS_DIR,
    scores_file: Annotated[
        Path, typer.Option(help="Judge scores CSV")
    ] = SCORES_DIR / "judge_scores.csv",
) -> None:
    """Show judge reasoning for specific files."""
    prompt_runs = _load_prompt_runs(runs_dir)
    _validate_prompt(prompt, prompt_runs)

    runs = prompt_runs[prompt]
    models, filenames, pivot, run_for_model, dir_by_run = _build_pivot(runs, scores_file)

    if not filenames:
        print(f"No judge scores found for prompt '{prompt}'.")
        raise typer.Exit(1)

    # Filter models
    if model:
        if model not in models:
            print(f"Model '{model}' not found. Available: {', '.join(models)}")
            raise typer.Exit(1)
        models = [model]

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

    lines = [f"## Notes: {prompt}"]
    if model:
        lines[0] += f" ({model})"
    lines.append("")

    for fn in matched:
        short = fn[:60] + "…" if len(fn) > 60 else fn
        lines.append(f"### {short}")
        lines.append(f"original: {samples_dir / fn}")
        for m in models:
            rn = run_for_model.get(m, "")
            lines.append(f"stripped ({m}): {runs_dir / rn / fn}")
            entry = pivot[fn].get(m)
            if entry:
                dim_strs = []
                for f in SCORE_FIELDS:
                    v = entry.get(f, "?")
                    dim_strs.append(f"{f[0].upper()}{v}")
                score_str = "/".join(dim_strs)
                reasoning = entry["reasoning"] or "(no reasoning)"
                lines.append(f"  score: {score_str}")
                lines.append(f"  reasoning: {reasoning}")
            else:
                lines.append(f"  (not scored)")
        lines.append("")

    print("\n".join(lines))
