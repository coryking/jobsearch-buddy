# Distill prompt iteration — handoff (2026-05-05)

Branch: `worktree-distill-eval-harness` (worktree at `.claude/worktrees/distill-eval-harness`)

This document hands off the in-progress distill prompt work. Read this first
before iterating further.

## Current state

**Leading candidate**: `prompts/distill-v3.1.txt` paired with **gpt-5.4-nano-medium**.

- Backfill cost (~98K active jobs): ~$280
- Latency: ~14s/job
- Citizenship gate, named entities, JD self-descriptions ("relentless operators
  who thrive in ambiguity") all preserved across the 12-job test set
- 12/12 successful, no meta-section regressions, zero bio leaks

**Production wiring**: `src/jobbuddy/sync/distill.py` already loads prompts
from `prompts/{version}.txt` via `JOBBUDDY_DISTILL_PROMPT_VERSION` env var
(default `distill-v1`). To deploy v3.1, set
`JOBBUDDY_DISTILL_PROMPT_VERSION=distill-v3.1` and `JOBBUDDY_DISTILL_MODEL=gpt-5.4-nano`
plus `reasoning_effort=medium` (or use the `gpt-5.4-nano-medium` registry entry
in production code if mirroring eval).

## What's been done

### Prompt evolution

- `prompts/distill-v1.txt` — original (deletion-only frame, "verbatim where
  possible"). Failed: kept corporate scripts ("Models and delivers a
  distinctive and delightful customer experience"), failed to use bio context.
- `prompts/distill-v2-synthesis.txt` — emic stance + synthesis. Worked but
  had auditor-mode regression on Anthropic JDs (model wrote "What the posting
  does not specify" subsections).
- `prompts/distill-v2.1.txt` — added salary placeholder rule + register voice
  tightening. Same auditor-mode tic.
- `prompts/distill-v3.txt` — **rewrite-frame** as master verb. Killed
  meta-sections; loosened citizenship gate handling and dropped hustle voice
  from short_jd.
- `prompts/distill-v3.1.txt` — added explicit short_jd content categories
  (hard gates / named entities / culture self-descriptions JD itself flags)
  + "Public-Sector Engineer doesn't tell a non-citizen they can't apply"
  example. Fixed citizenship regression and partial-fixed hustle voice.

### Code changes (commits on this branch ahead of main)

- `ff66b0a` — `research.py` SYSTEM_PROMPT register paragraph (industry
  journalist briefing, quote primary sources with attribution)
- `a3462ca` — distill prompt iteration v2→v3.1 + eval harness improvements
  (multi-model `-m` flag, plaintext output format, JSON parse on malformed
  raises, corrected pricing per playground memory)
- `b64a91c` — cache-friendly user_message ordering and `cached_tokens`
  tracking in both eval and production distill phase

### Eval harness now has

- Multi-model runs in one command: `jsb-eval run <ids> --prompt P -m M1 -m M2`
- `cached_tokens` column in `run_stats.csv`
- Plaintext output files under `eval/data/runs/{prompt}-{model}/{slug}-{job}.txt`
  with sections: TITLE / COMPANY / LOCATION / ATS-PROVIDED SALARY / COMPANY BIO
  / JOB DESCRIPTION / DISTILL: SHORT_JD / DISTILL: DESCRIPTION_NORMALIZED /
  DISTILL: SALARY
- Pricing for gpt-5.4 family, grok-4-fast variants, all corrected per
  `~/.claude/memory/azure_openai_playground_pricing.md`

## The 12-job test set

Pass these `job_ids` to `jsb-eval run` (company derived from DB join). All
have descriptions, all have company `long_bio` populated.

```
5174556008    anthropic    User Experience Researcher                  ai-lab
5205704008    anthropic    Staff+ Software Engineer, Public Sector     ai-lab + government
5115035007    anduril      Aviation Maintenance Engineer               defense + ITAR
5103761007    anduril      Senior Quality Engineer (Flight Line Support)  defense
94422787296   boeing       Tooling Mechanic - Quality                  aerospace trade + clearance
93545575584   boeing       Process Mechanic-Tube Bender                aerospace trade
94415590496   walgreens    Pharmacy Technician / Apprenticeship        regulated retail
94340770640   walgreens    Pharmacy Customer Service Associate         regulated retail hourly
30295         rei          Store Sales Specialist (Columbus-Dublin OH) retail hourly
0A6060F67BF9458DB42675C06A33E054  alaskaair  Ramp & Customer Service Agent  unionized airline
63B3BDF1F47C4805922087C31974B4AB  alaskaair  Stores/Warehouse Agent         unionized airline
c3830ffd-265d-46f4-b6b3-997f16781068  david-ai  Data Product Operations Lead  hustle-shop AI startup
```

The David AI job is the hustle-voice test case (relentless operators / Barry's
classes / "high-intensity environments" screening). Anthropic public-sector is
the citizenship-gate test case.

## Cross-model results at v3.1 (corrected pricing)

| Model | Backfill | Latency | Hustle voice in short_jd | Citizenship gate | Status |
|---|---|---|---|---|---|
| gpt-5.4-nano-medium | $280 | 14.0s | ✅ verbatim quote | ✅ | **leading candidate** |
| gpt-5-nano-medium | $299 | 20.0s | 🟡 partial | ✅ | dominated by 5.4 — strike |
| DeepSeek-V3.2 | $348 | 28.0s | 🟡 partial | ✅ | non-reasoning baseline — keep for comparison |
| gpt-5.4-nano-high | $362 | 15.1s | ❌ chose bio entities (Converse/Atlas/Chorus/Dialog) instead | ✅ | alternate (different priorities) |
| gpt-4.1-mini | $409 | 6.5s | ❌ paraphrased | ✅ | strike |
| gpt-5-mini (low) | $547 | 14.1s | 🟡 partial | ✅ | strike |
| gpt-5-nano-high | $735 | 75.6s | ✅ longest verbatim | ✅ | strike (dominated by 5.4-nano-medium) |
| gpt-5.4-mini (low) | $1,188 | 3.9s | 🟡 partial | ✅ | speed champion but expensive |
| gpt-5.4-mini-high | $6,191 | 22.8s | ✅ verbatim | ✅ | strike (cost prohibitive) |

`grok-4-fast-reasoning` failed (TPM/response_format issue — needs
investigation; bumping deployment TPM may unblock).

`DeepSeek-V3.2` and `grok-4-fast-reasoning` both need
`response_format={"type": "json_object"}` in their `api_params` (already added
to `models.py`).

## What's NOT yet validated

We have a working hypothesis, not a validated production claim. Sample size
per cell is one (one job per shape per model). Generalization is
extrapolation. Specifically:

- **License-name preservation** — A&P, PTCB, CDL, OSHA, PE stamp. Not
  systematically tested.
- **Drug-test scope detail** — Boeing's full list (marijuana / cocaine /
  opioids / amphetamines / PCP / alcohol). Not checked.
- **Multi-regulation jobs** — healthcare role with HIPAA + FDA + DEA. None
  in test set.
- **Hallucination edge cases** — vague JD + bio that doesn't disambiguate.
- **Compression integrity** — description_normalized should be shorter than
  input description. Not measured rigorously.
- **JSON validity at tail** — what % of jobs across 100s of samples actually
  return valid structured output across each model?
- **Latency p99** — averages are one thing; tail behavior matters at scale.
- **Job-shape diversity** — current 12 jobs miss: sales/GTM, customer
  success, healthcare/nursing, skilled trades (welder/electrician/HVAC),
  truck driving, GS-grade government, academic.

## Open work for the next session

In priority order:

1. **Expand eval set to 25-30 jobs** covering missing shapes (healthcare,
   skilled trades, sales, gov). Pick jobs that test specific dimensions —
   e.g. a JD with HIPAA+FDA+DEA together, a JD with 9+ named pieces of
   equipment.
2. **Run gpt-5.4-nano-medium + gpt-5.4-nano-high + DeepSeek-V3.2 (control)
   on expanded set** with v3.1 unchanged. Surface specific failure modes.
3. **Write v3.2 only if a specific failure mode emerges** across the broader
   set — not before. The temptation will be to over-engineer the prompt;
   resist unless eval data actually demands it.
4. **Fix grok-4-fast-reasoning** — TPM bump or response_format omission
   experiment. One more model data point at a different reasoning
   architecture.
5. **Cache-hit verification** — once production sync runs distill at scale,
   confirm `prompt_tokens_details.cached_tokens` is firing. Sync needs to
   poll jobs grouped by `(company_slug, location)` for cache to actually
   hit (currently uncertain). The v3.1 prompt + reordered user_message is
   ready for this; it's a sync-side task.
6. **Move research.py SYSTEM_PROMPT to `prompts/company-research-v1.txt`**
   for symmetry with distill.py. Stale file there now needs replacement.

## Decisions still owed before production

- Pick model for production: gpt-5.4-nano-medium (recommendation) vs.
  gpt-5.4-nano-high (entity-priority alternative) vs. DeepSeek-V3.2
  (non-reasoning baseline). All three need testing on the expanded set.
- Set `JOBBUDDY_DISTILL_PROMPT_VERSION` to `distill-v3.1` once production
  model is locked.
- Decide whether to also test temperature variants on the chosen model
  (currently using registry defaults; reasoning_effort is the lever for
  gpt-5.x).

## Reference

- Pricing source: `~/.claude/memory/azure_openai_playground_pricing.md`
  (re-query if quoting numbers in production decisions; prices drift)
- Eval cost lookup pattern: `~/.claude/projects/-Users-coryking-projects-jobsearch-buddy/memory/eval_cost_lookup.md`
- Production distill code: `src/jobbuddy/sync/distill.py`
- Eval harness: `src/jobbuddy/eval/`
- Run outputs: `eval/data/runs/distill-v3.1-{model}/`
