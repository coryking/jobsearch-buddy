# jobbuddy distill eval harness

Evaluates the distill prompt+model combination that powers `DistillPhase`
(`src/jobbuddy/sync/distill.py`). The distill phase produces three
fields per job — `short_jd`, `description_normalized`, `salary` — from
the job description plus the company's `long_bio` as context.
This harness measures whether a given prompt+model produces good
distillations.

The production defaults (set in `src/jobbuddy/settings.py`) are
`distill-v3.1` + `gpt-5.4-nano-high`; the harness lets you swap either
and compare.

## Entry point

```bash
jsb-eval --help
jsb-eval <subcommand> --help
```

`jsb-eval` is registered in `pyproject.toml` and dispatches to subcommands
defined in `src/jobbuddy/eval/`.

## Subcommands

| Command | Purpose |
|---------|---------|
| `run` | Run a prompt+model combo against a fixed list of `job_id`s. Pulls jobs from the production DB, requires `companies.long_bio` populated. Writes outputs + `run_stats.csv` per `(prompt, model)` directory. |
| `compare` | Side-by-side markdown diff of distill outputs across models for one prompt. Stdout-only, designed for eyeball spot-checking. |
| `judge` | LLM-as-judge auto-scoring of run outputs against a rubric. Writes `judge_scores.csv`. |
| `score` | Interactive Rich TUI for manual scoring. Writes `manual_scores.csv`. Supports resume. |
| `ground-truth` | Walks samples, opens `$EDITOR` for hand-edited references. Used to calibrate the judge. |
| `results` | Reads CSVs and emits aggregate JSON for jq / LLM consumption. `results summary <prompt>` and `results notes <prompt> <substring>...`. |

Run `jsb-eval <subcommand> --help` for current flags. Don't duplicate flag
documentation here — it rots.

## Directory layout

```
eval/
  CLAUDE.md            # this file
  prompts/             # versioned prompt files (text)
    judge.txt          # judge system prompt
    distill-v3.1.txt   # current production distill prompt (canonical copy)
    ...                # historical prompts
  data/                # gitignored; all disposable
    runs/<prompt>-<model>/   # per-combo outputs + run_stats.csv
    scores/                  # manual_scores.csv, judge_scores.csv
    ground-truth/            # hand-edited reference files
```

`src/jobbuddy/eval/` holds the code; `eval/` holds prompts and run data.

## Scoring rubric

Four dimensions, 1-5 each:

| Dimension | What it measures |
|-----------|------------------|
| **Recall** | Did differentiating signal survive? Most important — info loss removes a job from search results entirely. |
| **Precision** | Was boilerplate / generic content removed? Noise hurts ranking but doesn't cause total misses. |
| **Integrity** | Any fabricated content? False signal poisons the index. |
| **Fidelity** | Does kept text match original surface form? Lowest weight; the distill outputs aren't shown to users verbatim. |

The judge model is configured in `prompts/judge.txt` and called by `judge`
on each run's outputs. For judge calibration, run `score` on the same
samples manually, then `compare` the two CSVs.

## Models

Registered in `src/jobbuddy/eval/models.py` as `ModelConfig` entries.
Logical names map to Azure deployment names (which differ due to
naming restrictions). Add new models there.
