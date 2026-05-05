---
title: Job Search Redesign — Phase 1 (Distill Pipeline + MCP Surface Rebuild)
type: feat
status: active
date: 2026-05-05
origin: docs/brainstorms/2026-05-05-job-search-redesign-requirements.md
---

# Job Search Redesign — Phase 1 (Distill Pipeline + MCP Surface Rebuild)

## Execution Status

| Unit | Status | Notes |
|------|--------|-------|
| 1 — Schema migration | **Done** (commit `030c6ca`) | Folded with Unit 7. Migration 011 applied to test DB. Index named `idx_jobs_needs_distill`. |
| 2 — `sync/distill.py` | **Done** | `DistillPhase` wired into orchestrator. Reads `companies.long_bio` cache at phase start, calls strict-JSON-schema chat completions, writes `short_jd`/`description_normalized`/`salary` via `update_job_distill`. |
| 3 — MCP surface rebuild | **Done** | `JobStore.search_jobs_fts` (FTS+ts_rank, no diversity cap), `core.search_cached_jobs` shared helper, `JobSearchResults` includes `short_jd` column, MCP `search_jobs` renamed `since`→`posted_since` and gained `limit` (default 20, cap 100), `get_job_post_details` exposes `distilled: bool`, CLI `jsb search` switched to `--query`/`--posted-since`. Tests: `tests/test_search.py` (15 cases). |
| 4 — MCP tool descriptions | **Pending** | Depends on Unit 3. |
| 5 — Eval harness rewrite | **Pending** | Depends on Unit 2. |
| 6 — Prompt tuning loop | **Pending** | Depends on Unit 5. Open-ended; probably its own session. |
| 7 — Remove dead embedding stack | **Done** (commit `030c6ca`) | Folded with Unit 1 — the schema cascade forced it. |
| Follow-up — SERP tuning | **Pending** | Per-company diversity, FTS-ranked pagination, ts_rank weight tuning. Deferred from Phase 1 by explicit decision. |

**Naming change since plan was first written:** the LLM phase is now called **distill**, not "extract". Rationale in the Unit 1 commit message. References throughout the plan have been updated.

**Phase 2 dependency now resolved (merged):** the company-bio pipeline (former "Phase A") was merged into this branch. `companies.short_bio`/`long_bio` columns + `JobStore.update_company_bio` + `ResearchPhase` + `jsb research-companies` CLI are all live. Migration 012 is already applied to prod (011 is not). Unit 2's distill prompt reads `Company.long_bio` directly — no defensive `getattr` needed.

**Backfill ordering (when shipping Unit 2):**
1. Apply migration 011 in prod (`jsb migrate`) — drops embedding stack, adds `short_jd`/`description_normalized`.
2. Run `jsb research-companies` to fill `companies.long_bio` for every company (distill prompt depends on it).
3. Run `jsb sync distill` (or full sync) to backfill `short_jd`/`description_normalized` for every active job.

Reverse order would distill jobs against empty `<company_bio>` slots, producing weaker `short_jd` output that would need to be re-distilled later.

**Open question for next session:** the distill prompt currently treats `<company_bio>` as inert context (paste-through). It should explicitly direct the model to *synthesize* bio + JD when producing `short_jd` / `description_normalized` — elevate company-specific signal (mission, sector, posture) into the per-job output. Worth a focused prompt-engineering pass before Unit 5/6 eval work.

## Overview

Replace the embedding-dependent strip pipeline with a single structured-output
**distill pipeline** that produces three fields per job (`short_jd`,
`description_normalized`, `salary`). Reshape the MCP surface so it returns
fact-dense rows the calling LLM can rank without re-fetching JDs. Drop the
job embedding stack cleanly.

The brainstorm at `docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
covers more than this plan does. This plan is deliberately scoped to the
**job-side rebuild only**. Company-side work is deferred to Phase 2 (see
"Phase 2 (deferred)" below).

## Problem Frame

The MCP server's job is to give a calling LLM (Claude Desktop, ChatGPT)
enough comparable, normalized signal to filter and rank jobs without
re-fetching JDs. Today it returns thin rows (`company + title + location`)
backed by an empty-in-prod embedding pipeline that the calling LLM ignores
because it issues keyword-shaped queries. The strip-then-embed stack is
paying its per-job LLM cost with no downstream payoff. (See origin doc
"Background and motivation".)

This plan replaces that stack with one distill LLM call per job that
produces three artifacts the MCP surface actually returns, and rebuilds the
MCP surface around those artifacts plus PostgreSQL FTS.

## Requirements Trace

- R1. `search_jobs` returns rows containing enough signal (`short_jd`,
  salary, posted date) to filter without re-fetching the JD. (origin
  §"Reframe", §"MCP tool surface changes")
- R2. `get_job_post_details` returns `description_normalized`, never the
  raw posting. (origin §"MCP tool surface changes")
- R3. `search_jobs` supports `query` (FTS over `title + short_jd +
  description_normalized`) and `posted_since`. (origin §"MCP tool
  surface changes")
- R4. Job distill pipeline runs as one structured-output LLM call per job.
  (origin §"Job distill pipeline")
- R5. Job embedding infrastructure is removed: tables, indexes, modules,
  CLI, and migration scaffolding for embeddings are dropped. (origin
  §"Removed work")
- R6. `pgvector` extension and the `companies` table remain installed —
  Phase 2 will use them. (origin §"Schema changes")
- R7. NPOV is enforced in the distill prompt and evaluated by the new eval
  rubric. (origin §"NPOV", §"Eval")
- R8. The eval harness scores each of the three distill outputs
  independently with appropriate rubrics. (origin §"Eval")

## Scope Boundaries

- **All company-side work is out of scope.** No new company columns, no
  research pipeline, no `get_company` MCP tool, no inline-bio toggle on
  `search_jobs`. See "Phase 2 (deferred)" below.
- **No new structured per-job enums** (seniority, remote_policy,
  tech_tags). All such signal lives in `short_jd` prose.
- **No salary normalization.** `salary` is free-text in the form the JD
  used.
- **No company-bucket / saved-list feature.** Tracked separately. (origin
  §"Open questions deferred to planning")
- **No registry expansion to non-tech companies.** Tracked separately.

### Deferred to Separate Tasks

- Sibling-JD context for the distill phase (Phase 1.5 if `short_jd`
  output is too generic) — origin §"Open questions"
- Post-launch removal of `eval/prompts/v9-surgical-benefits.txt` once the
  new eval is validated end-to-end
- **SERP tuning** — per-company diversity cap, FTS-ranked pagination
  (cursor doesn't compose with `ts_rank` as primary sort), and
  ts_rank weight tuning against the new `short_jd` content. Phase 1
  ships pure `ts_rank` (when query set) / `published_at DESC` (otherwise)
  with simple `LIMIT` — no diversity cap, no cursor for FTS queries.
  Premise: the calling LLM issues keyword queries; semantic reasoning
  happens in the LLM over the returned `short_jd` rows. SERP quality
  is a tuning layer on top of correct FTS, not Phase 1 correctness.

## Context & Research

### Relevant Code and Patterns

- `src/jobbuddy/sync/strip.py` — current strip phase; pattern for the new
  `distill.py`. Same `WorkerPhase` shape (DB-poll, threadpool workers,
  write queue).
- `src/jobbuddy/sync/base.py` — `WorkerPhase` ABC the new phase extends.
- `src/jobbuddy/sync/embed.py` — read once for the rate-pacing pattern
  and the race-condition lessons in its module docstring, then delete.
- `src/jobbuddy/sync/__init__.py` — orchestrator (`sync_jobs`,
  `validate_sync_config`); update phase list.
- `src/jobbuddy/mcp_server.py` — current tool surface (`search_jobs`,
  `get_job_post_details`, `lookup_by_name`, `companies`, `log_*`).
- `src/jobbuddy/store.py` — `JobStore` data access; needs new methods for
  distill polling and reads of the new fields.
- `src/jobbuddy/openai_client.py` — Azure-aware OpenAI client factory.
- `src/jobbuddy/migrations/` — numbered SQL files; existing 007a–007f
  scaffolding to roll back.
- `prompts/distill-v1.txt` — already drafted from the brainstorm; load
  at runtime via `Path.read_text()`. Construct the user-message inputs
  per the XML-tag schema declared at the top of that file.
- `eval/` — current strip-eval harness. Reuse the harness shape; rewrite
  the runner and judge for three-output distill scoring.

### Institutional Learnings

- **Eval-of-evals**: LLM-as-judge means iterating two prompts. Multi-
  dimensional rubrics tell you *what* to fix. Don't use the same model
  family to judge its own output. (memory: `MEMORY.md` §"Eval Lessons
  Learned")
- **Migration pending-trigger constraint**: PG won't apply DDL inside a
  transaction that has pending FK trigger events. Splitting migrations
  was forced for 007. Likely relevant when the FTS rebuild and column
  rename land. (memory: `migration_pending_triggers.md`)
- **Azure embedding starvation (unresolved)**: hash-churn loop suspected.
  Phase 1's removal of embeddings makes this moot for jobs, but the
  lesson — unstable upstream fields cause silent re-work — applies to
  the distill phase's "is this row done?" predicate. Use a stable column
  presence check (`short_jd IS NOT NULL`), not a hash.
- **Strip eval hit diminishing returns**: prompt iteration at the strip
  level couldn't tell us if quality changes affected retrieval. Phase 1
  removes embedding-based retrieval entirely, so the new eval scores the
  thing that's actually returned.

## Key Technical Decisions

- **One LLM call per job, three structured outputs.** Folding `short_jd`,
  `description_normalized`, and `salary` into one call avoids three
  per-job round trips. The salary skip-branch via the prompt's
  `<ats_provided_salary>` flag is correct but rarely fires (3.5% fill
  rate). Rationale: cost and latency, not quality.
- **`description_stripped` is replaced by `description_normalized` in
  migration 011, and re-populated by the distill phase.** New column
  rather than dual-write because: (a) distill semantics differ
  meaningfully from strip (substance-preserved vs. boilerplate-removed),
  (b) every existing row must re-run distill anyway, (c) clean cut
  prevents the calling LLM from seeing mixed-quality outputs. Search
  matches over the JD body will be empty for un-distilled jobs until
  distill catches up — acceptable trade.
- **Job embeddings are removed, not deprecated-in-place.** Drop tables,
  index, module, CLI. Single rollback migration; do not edit existing
  ones. Rationale: dead code is a maintenance tax and a lure for future
  contributors. (origin §"Removed work")
- **`get_job_post_details` falls back to raw `description` when
  `description_normalized IS NULL`**, with a marker field
  (`distilled: false`). Consumer-visible behavior is degraded but not
  broken during the distill backfill window. (origin Open Question
  resolved here.)
- **Default `search_jobs` ordering when `query` is empty: `published_at
  DESC NULLS LAST`, tie-break on `(company_slug, job_id)` for
  determinism.** When `query` is set: `ts_rank` DESC with the same
  tie-break. (origin Open Question resolved here.)
- **Default `search_jobs` page size: 20; max 100; simple `LIMIT` for
  Phase 1 (no cursor, no diversity cap).** Cursor pagination on
  FTS-ranked queries doesn't compose cleanly with `ts_rank` as primary
  sort key, and per-company diversity is a SERP tuning concern — both
  are deferred. The calling LLM issues keyword queries and reasons
  over `short_jd` itself; ranking-quality work belongs in a follow-up.
- **Recommended distill model: `gpt-5-nano`**, kept as a setting
  (`JOBBUDDY_DISTILL_MODEL`). The existing strip-eval winner;
  re-validated by the new eval.
- **Eval reshape, not rewrite.** Keep `eval/run.py` orchestrator shape
  (parallel workers, judge dispatch, CSV output) but rewrite work-item
  shape, judge prompts, and CSV columns for three-field scoring.

## Open Questions

### Resolved During Planning

- Migration strategy for existing `description_stripped` data → drop the
  old column and add `description_normalized` in migration 011;
  re-populate via distill phase.
- Behavior of `get_job_post_details` for un-distilled jobs → return raw
  `description` with `distilled: false` marker.
- Default ordering for `search_jobs` when `query` empty → `published_at
  DESC NULLS LAST`.
- Pagination shape → simple `LIMIT` for Phase 1 (no cursor). Cursor
  + diversity belong to SERP tuning follow-up.

### Deferred to Implementation

- Exact JSON schema field types for the distill structured-output call —
  needs a real call against the Azure deployment to confirm the schema
  is accepted (Azure occasionally rejects schemas that pass strict-mode
  validation locally).
- Whether `description_normalized` should be stored as `TEXT` or `JSONB`
  with a structured "removed sections" sidecar for debuggability —
  depends on whether the eval needs the diff. Default to `TEXT`; revisit
  if eval rubrics need it.

## High-Level Technical Design

> *This illustrates the intended pipeline shape and is directional guidance
> for review, not implementation specification. The implementing agent
> should treat it as context, not code to reproduce.*

```
   jsb sync           ┌─────────────────────────────────────┐
                      │  fetch  →  enrich  →  distill       │
                      │  (HTTP)    (HTTP)     (LLM, no web) │
                      └─────────────────────────────────────┘
                                     │
                                     ▼
                            jobs.short_jd
                            jobs.description_normalized
                            jobs.salary

   MCP surface
     search_jobs(query, location, company, posted_since)
        → rows [{title, location, short_jd, salary, posted_at,
                 company_slug, ...}]
        → ranking: ts_rank when query, else published_at DESC

     get_job_post_details(company_slug, job_id)
        → {…, description: description_normalized OR raw,
              distilled: bool}
```

## Implementation Units

- [x] **Unit 1: Schema migration — add distill fields, drop embedding stack** *(shipped commit `030c6ca`, folded with Unit 7)*

**Goal:** Schema reflects Phase 1's job-side design: `short_jd` and
`description_normalized` exist on `jobs`; embedding tables and indexes
are gone; FTS column rebuilt over the new fields.

**Requirements:** R1, R2, R5, R6

**Dependencies:** None (foundation).

**Files:**
- Create: `src/jobbuddy/migrations/011_phase1_redesign.sql` (or split
  011a/011b/011c if pending-trigger constraints force it — see
  `MEMORY.md` §"migration_pending_triggers")
- Modify: `src/jobbuddy/migrations/__init__.py` (registry, if applicable)
- Test: `tests/test_store.py` (new fixtures verifying post-migration
  schema shape)

**Approach:**
- Add `short_jd TEXT` to `jobs` and replace `description_stripped` with
  `description_normalized TEXT`. Treat as drop + add of a new column
  rather than rename if the rename causes pending-trigger issues;
  existing data is regenerable so loss is acceptable.
- Drop: `job_embeddings` table, its HNSW index, `query_embeddings_cache`
  table (010), and any indexes on the old strip column. Do not drop
  `pgvector` extension. Do not drop or alter the `companies` table.
- Rebuild `jobs_fts_vector` (009) over
  `coalesce(title,'') || coalesce(short_jd,'') ||
   coalesce(description_normalized,'')`. The trigger that maintains the
  column needs the new column list.
- Index plan: GIN on the rebuilt FTS column; B-tree on
  `(published_at DESC, company_slug, job_id)` for default-ordering
  pagination.

**Patterns to follow:**
- `src/jobbuddy/migrations/009_fts_vector_column.sql` for FTS trigger
  shape.

**Test scenarios:**
- Happy path: After `jsb migrate`, `jobs.short_jd` and
  `jobs.description_normalized` exist with `TEXT` type;
  `job_embeddings` table does not exist.
- Happy path: `jobs_fts_vector` regenerates correctly when a row's
  `short_jd` is updated (trigger fires).
- Edge case: Migration applied twice is a no-op (idempotency check via
  `schema_migrations`).
- Error path: Migration on a database with embedding rows present
  succeeds and drops them cleanly (reproduce by inserting fixture rows
  into `job_embeddings` before applying 011).
- Integration: Existing fixture jobs survive the migration with
  `description` (raw) intact and `description_normalized = NULL` until
  re-distilled.

**Verification:**
- `jsb migrate` against a copy of prod-shape data succeeds.
- `\d jobs` shows the new columns; `\dt job_embeddings` errors.

---

- [x] **Unit 2: `sync/distill.py` — three-field distill phase**

**Goal:** A new `WorkerPhase` polls jobs missing `short_jd`, calls the
OpenAI-compatible API with `prompts/distill-v1.txt` and structured
output, writes `short_jd`, `description_normalized`, and `salary` back.
Replaces `sync/strip.py` in the orchestrator.

**Requirements:** R4, R7

**Dependencies:** Unit 1

**Files:**
- Create: `src/jobbuddy/sync/distill.py`
- Modify: `src/jobbuddy/sync/__init__.py` (register phase, update
  `SyncResult`, update `validate_sync_config` to require the OpenAI
  key for `distill`)
- Modify: `src/jobbuddy/store.py` (new `list_jobs_needing_distill` /
  `update_job_distill` methods; deterministic ORDER BY to prevent the
  unordered-LIMIT race documented in `sync/embed.py`)
- Modify: `src/jobbuddy/types.py` (new `DistillWorkItem`)
- Modify: `src/jobbuddy/cli/sync.py` (replace `strip` with `distill`
  in phase choices)
- Test: `tests/test_distill.py` (new), `tests/test_store.py` (distill
  query coverage)

**Approach:**
- Reuse `WorkerPhase` ABC verbatim. Threadpool workers, write queue,
  graceful shutdown — no new infrastructure.
- Construct the user message per the XML-tag schema declared at the
  top of `prompts/distill-v1.txt`. The `<company_bio>` slot is fed
  by `Company.long_bio` (merged from Phase A — already on this branch).
  Read directly: `company.long_bio or ""`. Empty is rare in practice
  (research backfill should run before distill backfill) but the
  prompt promises to handle empty regardless.
- Cache `{slug → long_bio}` once at `on_phase_start()` to avoid
  re-fetching companies per job. Bios change slowly; per-sync
  staleness is fine.
- Structured output: `response_format={"type": "json_schema",
  "json_schema": {...strict...}}` with three required fields, `salary`
  nullable. If Azure rejects nullable salary in strict mode, fall
  back to `{"type": "string"}` with empty-string convention.
- "Done" predicate: `short_jd IS NOT NULL` (stable, no hash). Avoids
  the Azure embedding-starvation churn class.
- Failure mode: on parse error or API error, leave all three fields
  null; next sync retries. Same posture as the deleted `sync/strip.py`.
- Model defaults to `JOBBUDDY_DISTILL_MODEL` (default `gpt-5-nano`),
  prompt version to `JOBBUDDY_DISTILL_PROMPT_VERSION` (default
  `distill-v1`). Both already exist on `Settings`.

**Execution note:** Implement test-first against a recorded
structured-output fixture; the prompt is large and spec-driven, so
unit tests should pin the JSON-schema contract and the user-message
input shape before wiring the live API.

**Patterns to follow:**
- `src/jobbuddy/sync/enrich.py` — DB-polling pattern; closest
  remaining `WorkerPhase` example after Unit 1+7 deleted strip/embed.
- The deleted `src/jobbuddy/sync/strip.py` (recoverable from git
  history pre-`030c6ca`) is the most direct analog — same threadpool
  + WriteQueue shape, same OpenAI-client init pattern.

**Test scenarios:**
- Happy path: Job with full inputs returns three fields populated and
  matching schema.
- Edge case: Prompt's salary-skip flag set true → `salary: null`
  regardless of JD content.
- Edge case: JD with no salary mention and the skip flag false →
  `salary: null`.
- Edge case: Very long JD (>50K chars) — current prompt has no fixed
  cap; verify model truncation behavior is graceful or raises.
- Error path: Schema-violating model output → row left null, retry on
  next sync.
- Error path: API timeout / rate-limit → row left null, error counted
  in `PhaseState`.
- Integration: Two-row fixture sync end-to-end; FTS index updates
  incorporate `short_jd` after distill completes.
- Integration: `list_jobs_needing_distill` is deterministic under
  concurrent worker polling (regression for the `sync/embed.py` race).

**Verification:**
- `uv run python -m pytest tests/test_distill.py -v` passes.
- `jsb sync distill --company anthropic` populates the three fields
  for fixture jobs.

---

- [x] **Unit 3: MCP tool surface — extend `search_jobs`, swap
  `get_job_post_details`**

**Goal:** MCP consumers see the new behavior. `search_jobs` returns
`short_jd` inline and supports `query` + `posted_since`.
`get_job_post_details` returns `description_normalized`.

**Requirements:** R1, R2, R3

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `src/jobbuddy/mcp_server.py`
- Modify: `src/jobbuddy/core.py` (new query helpers; keep Typer/FastMCP
  deps out)
- Modify: `src/jobbuddy/store.py` (new `search_jobs_fts` method)
- Modify: `src/jobbuddy/cli/search.py` (CLI parity — `jsb search` gains
  `--query`, `--posted-since`)
- Test: `tests/test_search.py` (rewrite — old `VectorSearch` tests
  will be deleted in Unit 6)

**Approach:**
- `search_jobs` parameters: `query` (FTS via `websearch_to_tsquery`
  over `fts_vector`), `companies`, `exclude_companies`,
  `location_filter`, `posted_since` (re-uses today's `since` parser:
  `7d`, `24h`, `2w`), `limit` (default 20, cap 100). No cursor in
  Phase 1.
- Ranking: when `query` is non-empty, `ts_rank` over FTS with
  `published_at DESC NULLS LAST` tie-break. When `query` is empty,
  pure `published_at DESC NULLS LAST`. No diversity cap. Drop any
  vestige of embedding similarity. SERP tuning (diversity, rank
  weights, cursor) is a deferred follow-up.
- Row shape: `{company_slug, company_name, job_id, title, location,
  short_jd, salary, posted_at, apply_url}`.
- `get_job_post_details` now reads `description_normalized` when
  present, raw `description` otherwise; returns `distilled: bool`.

**Execution note:** Test-first against the new `core.py` helpers; MCP
tool functions stay thin wrappers.

**Patterns to follow:**
- Existing `search_jobs` in `src/jobbuddy/mcp_server.py` for
  parameter-doc style.
- `src/jobbuddy/cli/search.py` for `--filter` parsing patterns.

**Test scenarios:**
- Happy path: `search_jobs(query="rust", posted_since="14d")` returns
  ranked rows with `short_jd` inline, all within the posted-since
  window.
- Happy path: `search_jobs(company="anthropic")` returns rows ordered
  by `published_at DESC`.
- Edge case: `posted_since="invalid"` → `ValueError` from `core.py`
  with a descriptive message; MCP returns the message string.
- Edge case: Job with `description_normalized=NULL` →
  `get_job_post_details` returns raw `description` and `distilled:
  false`.
- Error path: Postgres connection drop mid-query → exception bubbles
  to MCP, which returns an error string per existing convention.
- Integration: Cursor pagination — page 1 + page 2 return disjoint
  rows under inserts that happen between pages (cursor stability
  test).
- Integration: FTS query updates incorporate a freshly distilled job
  within one sync cycle.

**Verification:**
- `jsb-mcp` smoke test from Claude Desktop:
  `search_jobs(query="ml")` returns rows with `short_jd`, `salary`,
  `posted_at`.
- `jsb search --query ml --posted-since 7d` (CLI parity) shows the
  same data.

---

- [ ] **Unit 4: MCP tool descriptions + server instructions —
  prompt-engineering pass**

**Goal:** Tool descriptions and server-level `instructions` push the
calling LLM toward the new shape: use `query` + `posted_since`, prefer
inline `short_jd` over re-fetching.

**Requirements:** R1, R3

**Dependencies:** Unit 3

**Files:**
- Modify: `src/jobbuddy/mcp_server.py` (`@mcp.tool` docstrings, field
  `description`s, server `instructions=`)
- Test: manual eyeball via Claude Desktop; no automated test.

**Approach:**
- Server `instructions`: emphasize "this is a fact-dense data
  provider; the calling LLM is the ranker. Returned rows are not for
  direct user display." Trigger phrases: "find jobs at", "PM jobs at",
  "remote ML roles", "what about this one", etc.
- `search_jobs` docstring: lead with *when to use* (free-text search
  across cached jobs); call out that `query` is fuzzy text not
  embedding search; document `posted_since` examples.
- Field `description`s: examples for `posted_since`, valid values for
  `query`.

**Test scenarios:**
- N/A automated. Verification is qualitative — the calling LLM should
  stop re-fetching JDs when the row already has `short_jd + salary +
  posted_at`.

**Verification:**
- A Claude Desktop session searching for jobs makes ≤1
  `get_job_post_details` call per result (vs. today's pattern of one
  per row).
- The calling LLM uses `posted_since` rather than over-fetching and
  filtering client-side.

---

- [ ] **Unit 5: Eval harness — three-field distill scoring**

**Goal:** The eval harness scores the new distill pipeline (three
outputs, each with its own rubric). Strip-prompt-specific content is
deleted; harness shape and parallelism remain.

**Requirements:** R8

**Dependencies:** Unit 2 (need real outputs to score)

**Files:**
- Modify: `eval/run.py` (work-item shape, output CSV columns,
  multi-output dispatch)
- Modify: `eval/judge.py` (new judge prompts; multi-rubric scoring per
  job)
- Create: `eval/prompts/distill-judge-v1.txt`
- Delete: `eval/prompts/v1-` through `v9-` strip prompts (all)
- Delete: any strip-specific config files
- Modify: `eval/AGENTS.md` (update workflow to reflect new artifacts)

**Approach:**
- `run.py` work item now wraps a `Job` and produces three judge
  invocations (one per output field). Judge calls remain parallel.
- CSV columns: `job_id, model, prompt_version, short_jd_recall,
  short_jd_precision, short_jd_npov, jd_norm_recall, jd_norm_precision,
  jd_norm_integrity, jd_norm_fidelity, salary_correctness, ...`.
- Judge prompts: rewritten from scratch, multi-dimensional, with score
  anchors per `MEMORY.md` §"Eval Lessons Learned". Use
  `DeepSeek-R1-0528` as the default judge model (don't grade
  `gpt-5-nano` output with another `gpt-5-*` judge).
- Reuse the 75-worker default; respect the 1K RPM tier caps for
  judges.

**Patterns to follow:**
- Existing `eval/run.py` for orchestrator shape and accumulator
  pattern (the deferred smell #4 cleanup is **not** part of this
  unit; do it separately if it gets in the way).
- `MEMORY.md` §"Strip Prompt Eval: Conclusions" for the score-anchor
  approach.

**Test scenarios:**
- Happy path: A 5-job eval run produces a complete CSV with all
  rubric columns populated.
- Edge case: Job whose JD has no salary and the skip-flag is true →
  `salary_correctness` rubric correctly grades null as correct.
- Integration: Run on 30 jobs across 3 companies, baseline current
  `prompts/distill-v1.txt`. This becomes the seed for Unit 6 prompt
  iteration.

**Verification:**
- `jsb-eval --jobs 30 --model gpt-5-nano` runs end-to-end, produces a
  CSV.
- Aggregates published as a markdown table (current eval pattern).

---

- [ ] **Unit 6: Distill prompt tuning — distill-v1 → vN**

**Goal:** Iterate `prompts/distill-v1.txt` against the new distill
eval until scores converge. The user explicitly flagged this:
"we'll need some prompt tuning for the JD."

**Requirements:** R4, R7, R8

**Dependencies:** Unit 5

**Files:**
- Create: `prompts/distill-v2.txt`, `-v3.txt`, … as iteration
  proceeds. Keep all versions in version control so eval comparisons
  stay reproducible.
- Modify: `prompts/distill-v1.txt` only to fix typos; otherwise treat
  as immutable baseline.
- Modify: `src/jobbuddy/sync/distill.py` to read whichever version is
  configured via `JOBBUDDY_DISTILL_PROMPT_VERSION` (default to the
  most recent winner).
- No tests — eval is the test.

**Approach:**
- Run baseline eval on `distill-v1.txt`. Identify the lowest-scoring
  rubric.
- For each iteration: change one thing (one rubric, one principle).
  Re-run on the same fixture set. Compare. Promote if ≥0 change in
  every rubric and >0 in the targeted one.
- Apply the lessons from `MEMORY.md` §"Strip Prompt Eval: Conclusions":
  fixing one outlier often creates another with `gpt-5-nano`. Stop
  when net improvement plateaus, not when one rubric is "perfect."

**Test scenarios:** Eval *is* the scenario set. No additional tests.

**Verification:**
- The selected prompt version's eval scores are stored in
  `eval/results/`.
- The chosen version is documented in `prompts/CHANGELOG.md` (new
  file) with the eval CSV path that justified the choice.

---

- [x] **Unit 7: Remove dead embedding stack** *(shipped commit `030c6ca`, folded with Unit 1)*

**Goal:** Code, tests, and CLI commands tied to job embeddings are
gone. The repository is smaller and clearer.

**Requirements:** R5

**Dependencies:** Unit 3 (MCP no longer references the old code),
Unit 5 (eval no longer references the old code)

**Files:**
- Delete: `src/jobbuddy/sync/strip.py`
- Delete: `src/jobbuddy/sync/embed.py`
- Delete: `src/jobbuddy/embeddings.py`
- Delete: `src/jobbuddy/search.py` (`VectorSearch`)
- Delete: `src/jobbuddy/cli/generate_embed_text.py` (if present;
  `embed-test` command)
- Delete: `tests/test_embeddings.py`
- Delete: `tests/test_search.py` *only after* Unit 3 has its own
  replacement test file
- Modify: `src/jobbuddy/sync/__init__.py` (drop strip/embed phase
  registration; `validate_sync_config` no longer requires OpenAI key
  for embeddings)
- Modify: `src/jobbuddy/cli/sync.py` (remove `strip` / `embed` phase
  choices, surface `distill` instead — most of this lands in Unit 2)
- Modify: `src/jobbuddy/settings.py` (remove `embedding_model`,
  `strip_model`, `strip_batch_size` if no longer referenced)
- Modify: `pyproject.toml` if removing dependencies (the `pgvector`
  Python client may be deletable; the SQL extension stays).
- Modify: `AGENTS.md` (update file map and sync-pipeline section)

**Approach:**
- Rip out, don't deprecate. The brainstorm chose a clean cut.
  Backwards-compat shims would only complicate the new path.
- Verify nothing else imports the deleted modules:
  `rg "from jobbuddy.embeddings|from jobbuddy.search|VectorSearch"`.
- Update `AGENTS.md` package structure section to match.

**Test scenarios:**
- Happy path: `uv run python -m pytest tests/ -v` passes after
  deletions.
- Happy path: `jsb --help` no longer lists `embed-test`;
  `jsb sync --help` no longer lists `strip` / `embed`.
- Edge case: `jsb sync` (no phases) runs `fetch enrich distill` and
  exits clean with zero embedding-related warnings.

**Verification:**
- `rg "embedding|VectorSearch|short_jd"` shows hits only in expected
  places (settings, distill, store, MCP).
- Final commit diff is net-negative LOC.

## System-Wide Impact

- **Interaction graph:** Distill phase writes three new fields to
  `jobs`. MCP `search_jobs` and `get_job_post_details` shift from
  reading `description_stripped` to reading `description_normalized`
  and `short_jd`. The FTS trigger re-fires on every `UPDATE` of
  `short_jd` or `description_normalized`.
- **Error propagation:** Distill failures leave row fields null and
  retry on next sync invocation. MCP reads tolerate nulls (degraded
  but not broken).
- **State lifecycle risks:** During the distill backfill window,
  `search_jobs` is partial — rows with `short_jd IS NULL` will not
  match `query` searches over their JD. The `posted_since` and
  `company` filters still work. Acceptable trade per Key Decisions.
- **API surface parity:** `jsb search` CLI and `search_jobs` MCP tool
  must stay in lockstep on the new filters — both sit on top of the
  same `core.py` helpers.
- **Integration coverage:** Tests must cover (a) distill write →
  trigger → FTS update → search match, (b) MCP cursor stability under
  concurrent inserts.
- **Unchanged invariants:**
  - Raw `description` column is preserved on every job. The brainstorm
    explicitly keeps it as the audit / re-distillation source.
  - `lookup_by_name`, `companies`, `log_job_application`, and
    `log_job_activity` MCP tools are unchanged.
  - `pgvector` extension stays installed; `companies` table stays
    unchanged.
  - Existing fetcher contracts and ATS integrations are untouched.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Distill pipeline produces lower-quality `short_jd` than the prototype on edge-case JDs (multi-role postings, very short JDs, non-English JDs) | Eval harness covers this in Unit 5; prompt iteration in Unit 6. Worst case: ship with `distill-v1` and iterate post-launch. |
| Search is empty for un-distilled jobs during backfill window | Backfill before flipping the MCP tool descriptions to advertise `query`. Sequence: Unit 2 runs to completion on prod data, then Unit 4 lands. |
| Migration 011 trips PG pending-trigger constraint (per `MEMORY.md`) | Pre-split into 011a/011b/011c if needed; rehearse on the dev DB. |
| Calling LLM ignores `short_jd` and keeps re-fetching JDs | Unit 4's tool-description prompt-engineering is the lever. Verify behavior in Claude Desktop before declaring Unit 4 done. |
| Removing embeddings while resume project depends on jobsearch-buddy as editable dep breaks resume's MCP usage | Resume project consumes `jsb-mcp` and `jsb` CLI, both of which are reshaped here. Smoke-test resume's flows after Unit 3 + Unit 7. |

## Documentation / Operational Notes

- Update `AGENTS.md`:
  - Package structure section (no `strip.py`, `embed.py`,
    `embeddings.py`, `search.py`; new `distill.py`)
  - Sync pipeline section (three phases not four)
  - Schema migrations section (note the 011 split if it happens)
- Update `docs/architecture.md`:
  - Strip → Distill pipeline shape diagram
  - Removal of vector search; replacement with FTS
- Update `docs/throughput-reference.md` with new distill throughput
  numbers (likely similar to strip; confirm post-launch).
- Add `prompts/CHANGELOG.md` to track prompt iterations.
- `MEMORY.md`: After Phase 1 ships, resolve the Azure embedding
  starvation entry — embeddings are gone.
- Operational rollout:
  1. Apply migration 011 in dev → prod after rehearsal.
  2. Run `jsb sync distill` to backfill `short_jd` and
     `description_normalized` for all jobs.
  3. Deploy MCP changes (Unit 3 + Unit 4).
  4. Remove dead code (Unit 7) once consumers (resume project) are
     verified.

## Phase 2 (deferred)

The brainstorm at
`docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
sketches a Phase 2 layer that builds on the job-side surface this plan
delivers. That work is **explicitly out of scope here** and will be
scoped, prompt-tuned, and evaluated in its own plan when the time
comes. Phase 1 leaves the door open for it: the `pgvector` extension
remains installed, the `companies` table remains unchanged, and
nothing in the schema or MCP surface this plan delivers precludes
adding more later.

## Sources & References

- **Origin document:**
  `docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
- Prompt artifact in repo: `prompts/distill-v1.txt`
- Memory: `MEMORY.md` §"Strip Prompt Eval: Conclusions",
  §"Eval Lessons Learned", §"PostgreSQL + pgvector",
  §"migration_pending_triggers", §"Azure embedding starvation"
- Related code anchors:
  - `src/jobbuddy/sync/strip.py`, `src/jobbuddy/sync/embed.py`,
    `src/jobbuddy/sync/base.py`, `src/jobbuddy/sync/__init__.py`
  - `src/jobbuddy/mcp_server.py` (tools at lines 71, 247, 306, 408)
  - `src/jobbuddy/store.py`, `src/jobbuddy/openai_client.py`
  - `src/jobbuddy/migrations/004_companies.sql`,
    `009_fts_vector_column.sql`, `007a–007f`, `010`
  - `eval/run.py`, `eval/judge.py`
