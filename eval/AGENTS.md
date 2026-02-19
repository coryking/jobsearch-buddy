# Strip Eval Harness

Systematic evaluation of LLM-based boilerplate stripping from job descriptions.
Compares prompt variants and models to find the best combo for the `StripPhase`
in `src/jobbuddy/sync/strip.py`.

## The Problem

The job search index contains thousands of postings from 100+ companies across
diverse industries. Users search across ALL companies with natural language
queries ("robotics engineer with security clearance near DC", "PM roles at
companies working on internet privacy"). Each posting is stripped of boilerplate,
then embedded into a vector for semantic search.

Boilerplate — EEO statements, generic benefits, legal disclaimers, accommodation
instructions — appears nearly identically across thousands of postings. When
embedded, this shared text dominates the vector and makes every job look alike,
drowning out the signal that differentiates one role from another. Stripping
removes the noise so the embedding captures what actually matters.

## What "Good" Looks Like

The stripped text should preserve everything that helps the right job surface
for the right query, and remove everything that makes jobs look the same.

The hard part is the gray areas:
- Company-specific content (Cloudflare's "Project Galileo", Anthropic's research
  directions) looks like marketing but differentiates the company in cross-company
  search — keep it
- Eligibility requirements (export control, citizenship, clearance) look like
  legal text but are hard gates that determine who can apply — keep them
- Salary ranges wrapped in legal framing ("pursuant to state law...") look like
  disclaimers but the numbers themselves are differentiating — keep the numbers
- Differentiated benefits (100% coverage, fertility benefits, unlimited PTO) look
  like the standard benefits list but set the company apart — keep them

The stripped text is never shown to users — it only feeds the embedding model.
So formatting, headers, and minor surface-form changes don't matter for search
quality. What matters is semantic content: was differentiating meaning preserved
and was noise removed.

## What We're Evaluating

Two variables: **strip prompt** (the instructions given to the LLM) and **strip
model** (which LLM executes it). The eval harness tests combinations of both
to find the best pairing for production use.

Quality is measured on four dimensions (see Scoring Rubric below). Recall
(preserving differentiating content) is the most important — information loss
means a job disappears from search results entirely, while leftover noise only
degrades ranking.

## Why This Exists

The `StripPhase` uses an LLM to remove boilerplate from ~20K job descriptions
before embedding. The prompt and model were chosen without evaluation. This
harness enables:

- Iterating on prompts with fast feedback
- Comparing models on quality vs. cost vs. latency
- LLM-as-judge auto-scoring (DeepSeek-R1-0528) with 4-dimension rubric
- Manual scoring for judge calibration and validation

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
    runs/<run-name>/     # stripped outputs + run_stats.csv per combo
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

Writes stripped outputs to `eval/data/runs/<run-name>/` plus `run_stats.csv`
(one row per completed sample with timing and token counts). Aggregates are
computed on read by `ats-eval results`.

### 4. Score manually

```bash
ats-eval score \
    --run eval/data/runs/v1-gpt4.1nano/
```

Shows original vs. stripped side-by-side with diff highlighting. Flags
suspicious removals. Prompts for 4 dimension scores (1-5 each). Saves to
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

Four dimensions, 1-5 each. Displayed as R/P/I/F (e.g., 5/4/5/3).

| Dimension | What it measures | Weight |
|-----------|-----------------|--------|
| **Recall** | Did differentiating content survive? (most important — info loss is irreversible) | Highest |
| **Precision** | Was boilerplate removed? (noise hurts ranking but doesn't cause total misses) | Medium |
| **Integrity** | Any fabricated content? (false signal poisons the index) | High when it fails |
| **Fidelity** | Does kept text match original surface form? (least important for search) | Lowest |

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

### run_stats.csv

Per-sample token and timing data, appended as each sample completes:
```
filename,input_chars,output_chars,prompt_tokens,completion_tokens,reasoning_tokens,total_tokens,elapsed_seconds
001-stripe-sr-backend-engineer.txt,4521,2103,1205,580,0,1785,3.412
```

Append-safe: partial re-runs add rows without destroying previous data.
Prompt and model are derived from the directory name (`{prompt_stem}-{model}`).

### CSV score files

`manual_scores.csv`:
```
filename,run_name,recall,precision,integrity,fidelity,notes
001-stripe-sr-backend.txt,v1-gpt4.1nano,5,5,5,5,clean removal
```

`judge_scores.csv`:
```
filename,run_name,recall,precision,integrity,fidelity,reasoning
001-stripe-sr-backend.txt,v1-gpt4.1nano,5,4,5,5,All boilerplate removed...
```
