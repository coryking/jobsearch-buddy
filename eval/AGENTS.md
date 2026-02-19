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
# Interactive — prompts for model and prompt file selection:
ats-eval run --run-name v1-gpt4.1nano

# Explicit — no interactive prompts:
ats-eval run \
    --prompt eval/prompts/v1-original.txt \
    --model gpt-4.1-nano \
    --run-name v1-gpt4.1nano
```

Writes stripped outputs to `eval/data/runs/v1-gpt4.1nano/` plus `run_meta.json`
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

### 8. Validate judge vs. human

Compare `judge_scores.csv` against `manual_scores.csv` on the ~10 ground-truth
files. If the judge agrees with you on those, trust it for the rest.

## Scoring Rubric

Three criteria, each 1-5:

### Boilerplate Removal

Did it remove the right stuff?

| Score | Meaning |
|-------|---------|
| 5 | All boilerplate removed (EEO, generic benefits, legal disclaimers, accommodation) |
| 4 | Nearly all removed, minor remnants |
| 3 | Most removed but some obvious sections remain |
| 2 | Significant boilerplate left in |
| 1 | Little to no boilerplate removed |

### Content Preservation

Did it keep the important stuff?

| Score | Meaning |
|-------|---------|
| 5 | All role-specific content preserved (responsibilities, quals, stack, comp, team) |
| 4 | Nearly all preserved, minor omissions |
| 3 | Most preserved but some meaningful details lost |
| 2 | Significant role-specific content removed |
| 1 | Critical content missing (responsibilities or qualifications gone) |

### No Hallucination

Did it avoid adding or rephrasing?

| Score | Meaning |
|-------|---------|
| 5 | Output is a strict subset of the original text, no rephrasing |
| 4 | Essentially faithful, trivial formatting changes only |
| 3 | Minor rephrasing or reordering but meaning preserved |
| 2 | Noticeable additions or rewording that changes emphasis |
| 1 | Fabricated content or substantial rewording |

## Models

| Deployment    | Type           | Capacity | Expected Latency |
|---------------|----------------|----------|------------------|
| gpt-5-nano    | Reasoning      | 5,000    | 3-7s             |
| gpt-5-mini    | Reasoning      | 1,000    | 3-7s             |
| gpt-4.1-nano  | Non-reasoning  | 5,000    | 0.3-0.7s         |
| gpt-4.1-mini  | Non-reasoning  | 5,000    | 0.3-0.7s         |
| DeepSeek-V3.2 | Non-reasoning  | 1,000    | 0.3-0.7s         |

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
filename,run_name,boilerplate_removal,content_preservation,no_hallucination,notes
001-stripe-sr-backend.txt,v1-gpt4.1nano,4,5,5,clean removal
```

`judge_scores.csv`:
```
filename,run_name,boilerplate_removal,content_preservation,no_hallucination,reasoning
001-stripe-sr-backend.txt,v1-gpt4.1nano,4,5,5,All boilerplate removed...
```
