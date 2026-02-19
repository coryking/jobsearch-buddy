# Strip Eval Harness

Systematic evaluation of LLM-based boilerplate stripping from job descriptions.
Compares prompt variants and models to find the best combo for the `StripPhase`
in `src/jobbuddy/sync/strip.py`.

## Why This Exists

The `StripPhase` uses an LLM to remove boilerplate (EEO statements, generic
benefits, legal disclaimers) from ~20K job descriptions before embedding. The
prompt and model were chosen without evaluation. This harness enables:

- Iterating on prompts with fast feedback
- Comparing models on quality vs. latency
- Both manual scoring and LLM-as-judge auto-scoring
- Validating that the judge correlates with human judgment

## Directory Layout

```
src/jobbuddy/eval/       # CLI subpackage (ats-eval entry point)
  cli.py                 # Typer app, registers subcommands
  models.py              # ModelConfig dataclass + KNOWN_MODELS registry
  extract.py             # ats-eval extract — stratified sample extraction
  run.py                 # ats-eval run — prompt+model against samples
  score.py               # ats-eval score — Rich TUI manual scoring
  judge.py               # ats-eval judge — LLM-as-judge auto-scoring
  ground_truth.py        # ats-eval ground-truth — interactive GT creator

eval/                    # data and config (this directory)
  AGENTS.md              # this file
  prompts/
    v1-original.txt      # production prompt (from strip.py)
    judge.txt            # judge system prompt
  data/                  # ← gitignored, all disposable
    samples/             # raw job descriptions + sample_manifest.json
    ground-truth/        # hand-stripped files (same names as samples/)
    runs/<run-name>/     # stripped outputs + run_meta.json per combo
    scores/              # manual_scores.csv, judge_scores.csv
```

## Workflow

### 1. Extract samples

```bash
ats-eval extract --count 25
```

Pulls active jobs with descriptions from the production DB, stratified
round-robin across companies. Writes `.txt` files and `sample_manifest.json`
to `eval/data/samples/`.

### 2. Create ground truth

```bash
ats-eval ground-truth
```

Interactive workflow: walks through each sample, lets you decide whether to
include it, and opens `$EDITOR` so you can hand-strip the boilerplate.
These are your reference for what "correct" looks like.

### 3. Run a prompt+model combination

```bash
# Full interactive — picks prompt (default: newest), then model checkboxes
# (models without existing runs are pre-checked):
ats-eval run

# Single model, interactive prompt picker:
ats-eval run --model gpt-5-nano

# Explicit — no interactive prompts:
ats-eval run \
    --prompt eval/prompts/v1-original.txt \
    --model gpt-4.1-nano \
    --run-name v1-gpt4.1nano
```

When run interactively, the prompt picker defaults to the most recently modified
prompt file. The model picker uses questionary checkboxes: models that already
have a run for the selected prompt appear with "(done)" and unchecked; remaining
models are pre-checked. Multi-selecting kicks off sequential runs.

Run names auto-generate as `{prompt_stem}-{model}` when `--run-name` is omitted.

Writes stripped outputs to `eval/data/runs/<run-name>/` plus `run_meta.json`
with per-file timing, token counts, and aggregate stats.

### 4. Score manually

```bash
ats-eval score \
    --run eval/data/runs/v1-gpt4.1nano/
```

Shows original vs. stripped side-by-side with diff highlighting. Flags
suspicious removals. Prompts for 3 scores (1-5 each). Saves to
`eval/data/scores/manual_scores.csv`. Supports resume (skip already-scored).

### 5. Iterate on prompts

Copy a prompt, edit it, re-run, re-score:

```bash
cp eval/prompts/v1-original.txt eval/prompts/v2-tighter.txt
# edit v2-tighter.txt
ats-eval run \
    --prompt eval/prompts/v2-tighter.txt \
    --model gpt-4.1-nano \
    --run-name v2-gpt4.1nano
```

Use gpt-4.1-nano for prompt iteration (cheap, fast). Switch to other models
for the final model comparison.

### 6. Scale up samples

Once the prompt is stable, extract a larger sample set:

```bash
ats-eval extract --count 100
```

### 7. Run LLM judge

```bash
ats-eval judge \
    --run eval/data/runs/v1-gpt4.1nano/
```

Auto-scores using gpt-5-mini. Writes to `eval/data/scores/judge_scores.csv`.

### 8. View results

```bash
# Interactive prompt picker:
ats-eval results

# Explicit prompt:
ats-eval results v3-why
```

Reads `judge_scores.csv`, groups runs by prompt, and outputs a plain-text
comparison: CSV score matrix (file × model) with MEAN/MEDIAN rows, followed
by per-file reasoning from each model. Output includes filesystem paths to
runs and scores for easy follow-up.

### 9. Validate judge vs. human

Compare `judge_scores.csv` against `manual_scores.csv` on the ~10 ground-truth
files. If the judge agrees with you on those, trust it for the rest.

## Scoring Rubric

Single score, 1-5: "How did it do?"

| Score | Meaning |
|-------|---------|
| 5 | All boilerplate removed, all important content preserved, no rephrasing |
| 4 | Minor issues — small boilerplate remnants or trivial omissions |
| 3 | Decent but noticeable issues — some boilerplate left or meaningful content lost |
| 2 | Significant problems — lots of boilerplate remaining or important content removed |
| 1 | Failed — little removed, critical content lost, or substantial hallucination |

## Models

Registered in `src/jobbuddy/eval/models.py`. The `ModelConfig` dataclass maps
logical model names to Azure deployment names (which may differ due to naming
restrictions) and API parameters.

| Model         | Deployment    | Type           | Capacity |
|---------------|---------------|----------------|----------|
| gpt-5-nano    | gpt-5-nano    | Reasoning      | 5,000    |
| gpt-5-mini    | gpt-5-mini    | Reasoning      | 1,000    |
| gpt-4.1-nano  | gpt-4.1-nano  | Non-reasoning  | 5,000    |
| gpt-4.1-mini  | gpt-41-mini   | Non-reasoning  | 5,000    |
| DeepSeek-V3.2 | DeepSeek-V3.2 | Non-reasoning  | 1,000    |

## File Formats

### sample_manifest.json

Maps filename to DB metadata:
```json
{
  "001-stripe-sr-backend-engineer.txt": {
    "company_slug": "stripe",
    "job_id": "abc123",
    "title": "Sr. Backend Engineer",
    "db_pk": 42
  }
}
```

### run_meta.json

Per-run metadata with timing:
```json
{
  "run_name": "v1-gpt4.1nano",
  "model": "gpt-4.1-nano",
  "prompt_file": "eval/prompts/v1-original.txt",
  "prompt_sha256": "a1b2c3d4e5f6",
  "timestamp": "2026-02-18T...",
  "sample_count": 25,
  "success_count": 25,
  "error_count": 0,
  "aggregates": {
    "mean_latency": 0.45,
    "median_latency": 0.42,
    "p95_latency": 0.71,
    "total_seconds": 11.2,
    "total_tokens": 48000
  },
  "files": [...]
}
```

### CSV score files

`manual_scores.csv`:
```
filename,run_name,score,notes
001-stripe-sr-backend.txt,v1-gpt4.1nano,5,clean removal
```

`judge_scores.csv`:
```
filename,run_name,score,reasoning
001-stripe-sr-backend.txt,v1-gpt4.1nano,5,All boilerplate removed...
```
