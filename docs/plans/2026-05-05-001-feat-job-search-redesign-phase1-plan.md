---
title: Job Search Redesign — Phase 1 (Extract Pipeline + Optional Company Bios)
type: feat
status: active
date: 2026-05-05
origin: docs/brainstorms/2026-05-05-job-search-redesign-requirements.md
---

# Job Search Redesign — Phase 1 (Extract Pipeline + Optional Company Bios)

## Overview

Replace the embedding-dependent strip pipeline with a single structured-output
**extract pipeline** that produces three fields per job (`short_jd`,
`description_normalized`, `salary`). Add a separate, **opt-in company-research
pipeline** that produces a per-company `short_bio` via the Azure Responses API
with `web_search` grounding. Reshape the MCP surface to be a fact-dense data
provider for an LLM ranker. Drop job embeddings cleanly.

The brainstorm at `docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
is the source of truth for behavior, scope, and stance. This plan resolves the
*how*: schema migrations, code structure, sequencing, eval rebuild, and
prompt-tuning loop. Phase 2 (`find_companies` semantic search) is deferred
and out of scope.

## Problem Frame

The MCP server's job is to give a calling LLM (Claude Desktop, ChatGPT)
enough comparable, normalized signal to filter and rank jobs without
re-fetching JDs. Today it returns thin rows (`company + title + location`)
backed by an empty-in-prod embedding pipeline that the calling LLM ignores
anyway because it issues keyword-shaped queries. The strip-then-embed stack
is paying its per-job LLM cost with no downstream payoff. (See origin doc
"Background and motivation".)

This plan replaces that stack with one extract LLM call per job that produces
three artifacts the MCP surface actually returns, plus an optional
company-bio layer that the calling LLM uses for vibe context.

## Requirements Trace

- R1. `search_jobs` returns rows containing enough signal (`short_jd`, salary,
  posted date) to filter without re-fetching the JD. (origin §"Reframe",
  §"MCP tool surface changes")
- R2. `get_job_post_details` returns `description_normalized`, never the raw
  posting. (origin §"MCP tool surface changes")
- R3. New `get_company(slug)` MCP tool returns `short_bio` plus basic
  metadata. (origin §"MCP tool surface changes")
- R4. `search_jobs` supports `query` (FTS over `title + short_jd +
  description_normalized`), `posted_since`, and `include_company_bio`.
- R5. Job extract pipeline runs as one structured-output LLM call per job,
  consumes optional `<company_bio>` context, and gracefully handles its
  absence. (origin §"Job extract pipeline")
- R6. Company research runs in two trigger contexts: (a) automatically
  when a new company is added to the registry (so every company arrives
  with a bio), and (b) via a backfill CLI command for existing companies
  and refresh-by-staleness. It is **never** part of the per-job `jsb sync`
  pipeline. (origin §"Company research pipeline", §"Trigger semantics")
- R7. Job embedding infrastructure is removed: tables, indexes, modules,
  CLI, and migration scaffolding for embeddings are dropped. (origin
  §"Removed work")
- R8. `pgvector` extension and the `companies` table remain installed for
  Phase 2. (origin §"Schema changes")
- R9. NPOV is enforced in both prompts and evaluated by the new eval rubric.
  (origin §"NPOV", §"Eval")
- R10. The eval harness scores each of the three extract outputs
  independently with appropriate rubrics, and grades `short_bio`
  separately. (origin §"Eval")

## Scope Boundaries

- **Phase 2 (`find_companies` semantic search) is out of scope.** No
  `embed_text` for companies, no `company_embeddings` table, no
  semantic-vibe MCP tool. (origin §"Phase 2")
- **No new structured per-company fields** (industry, size_bucket,
  maturity). The calling LLM infers from `short_bio` prose.
- **No new structured per-job enums** (seniority, remote_policy,
  tech_tags). All such signal lives in `short_jd` prose.
- **No automated company-bio refresh.** Refresh stays user-driven via
  `--stale Nd`.
- **No salary normalization.** `salary` is free-text in the form the JD
  used.
- **No company-bucket / saved-list feature.** Tracked separately. (origin
  §"Open questions deferred to planning")
- **No registry expansion to non-tech companies.** Tracked separately.

### Deferred to Separate Tasks

- Sibling-JD context for the extract phase (Phase 1.5 if `short_jd`
  output is too generic) — origin §"Open questions"
- Post-launch removal of `eval/prompts/v9-surgical-benefits.txt` once new
  eval is validated end-to-end

## Context & Research

### Relevant Code and Patterns

- `src/jobbuddy/sync/strip.py` — current strip phase; pattern for the new
  `extract.py`. Same `WorkerPhase` shape (DB-poll, threadpool workers,
  write queue).
- `src/jobbuddy/sync/base.py` — `WorkerPhase` ABC the new phase extends.
- `src/jobbuddy/sync/embed.py` — read once for the rate-pacing pattern,
  then delete. The race-condition lessons in its module docstring matter
  for the future Phase 2 `find_companies` embed pipeline; preserve them
  in commit history.
- `src/jobbuddy/sync/__init__.py` — orchestrator (`sync_jobs`,
  `validate_sync_config`); update phase list.
- `src/jobbuddy/mcp_server.py` — current tool surface (`search_jobs`,
  `get_job_post_details`, `lookup_by_name`, `companies`, `log_*`).
- `src/jobbuddy/store.py` — `JobStore` data access; needs new methods for
  extract polling, research polling, and reads of the new fields.
- `src/jobbuddy/openai_client.py` — Azure-aware OpenAI client factory;
  the research phase needs the **Responses API** path (not Chat
  Completions), with `web_search` tool support — cf. the working
  prototype at `scratchpad/foundry_company_research.py`.
- `src/jobbuddy/migrations/` — numbered SQL files; existing 007a–007f
  scaffolding to roll back.
- `prompts/extract-v1.txt`, `prompts/company-research-v1.txt` — already
  drafted from the brainstorm; load at runtime via `Path.read_text()`.
- `scratchpad/foundry_company_research.py` — **working prototype** for
  the research pipeline. Validates Azure Responses API + `web_search` +
  structured output + AAD bearer auth at ~$0.10/company. Use as the
  reference shape for `sync/research.py` (input format, schema, source
  capture via `include=["web_search_call.action.sources"]`, NPOV stance).
- `eval/` — current strip-eval harness. Reuse the harness shape; rewrite
  the runner and judge for three-output extract scoring + bio scoring.

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
  Phase 1's removal of embeddings makes this moot for jobs but the
  lesson — unstable upstream fields cause silent re-work — applies to
  the extract phase's "is this row done?" predicate. Use a stable column
  presence check (`short_jd IS NOT NULL`), not a hash.
- **Strip eval hit diminishing returns**: prompt iteration at the strip
  level couldn't tell us if quality changes affected retrieval. Phase 1
  removes embedding-based retrieval entirely, so the new eval scores the
  thing that's actually returned.

### External References

- Azure Responses API + `web_search` — empirically validated in the
  prototype runs in `scratchpad/runs/` at $14/1K Bing transactions and
  3-5 transactions/company. (origin §"Phase 1 — Cost framing")
- pgvector docs — only relevant for the *removal* of the HNSW index; no
  new pgvector code in Phase 1.

## Key Technical Decisions

- **One LLM call per job, three structured outputs.** Folding `short_jd`,
  `description_normalized`, and `salary` into one call avoids three
  per-job round trips. The salary skip-branch via `<ats_provided_salary>`
  is correct but rarely fires (3.5% fill rate). Rationale: cost and
  latency, not quality — extract quality is dominated by the prompt and
  model, not by per-call isolation.
- **Company research has two entry points: automatic on company-add,
  and a `jsb research-companies` backfill command.** `jsb sync` never
  invokes `web_search`. New companies get a bio at registration time
  so the registry is never partially populated going forward; the
  backfill command catches existing companies and handles `--stale Nd`
  refresh. Cost per company is low and validated by the prototype; no
  cost hedge is needed.
- **`description_stripped` is renamed via migration to
  `description_normalized` and re-populated by the extract phase.** New
  column rather than dual-write because: (a) extract semantics differ
  meaningfully from strip (substance-preserved vs. boilerplate-removed,
  see prompt diffs), (b) every existing row must re-run extract anyway,
  (c) clean cut prevents the calling LLM from seeing mixed-quality
  outputs. Search will be empty for un-extracted jobs until extract
  catches up — acceptable trade.
- **Job embeddings are removed, not deprecated-in-place.** Drop tables,
  index, module, CLI. Single rollback migration; do not edit existing
  ones. Rationale: dead code is a maintenance tax and a lure for future
  contributors. (origin §"Removed work")
- **`get_job_post_details` falls back to raw `description` when
  `description_normalized IS NULL`**, with a marker field
  (`extracted: false`). Consumer-visible behavior is degraded but not
  broken during the extract backfill window. (origin Open Question
  resolved here.)
- **Default `search_jobs` ordering when `query` is empty: `published_at
  DESC NULLS LAST`, tie-break on `(company_slug, job_id)` for
  determinism.** (origin Open Question resolved here.)
- **Default `search_jobs` page size: 20; max 100; cursor is
  `(published_at, company_slug, job_id)` opaque tuple.** Cheap, stable
  under inserts, no offset weirdness. (origin Open Question resolved
  here.)
- **`include_company_bio` is per-call boolean, default false.** Avoids
  inflating tokens for the common case where the calling LLM already
  has the bio cached or doesn't need it.
- **Research pipeline writes one row per company successful run** to
  `companies.short_bio` + `research_updated_at`. No per-search audit
  table — the JSON dumps from the prototype already write to
  `scratchpad/runs/`; production runs persist nothing beyond the bio
  itself (search trail is recoverable from logs if needed).
- **Recommended models, kept as settings (not hardcoded):**
  - Extract: `gpt-5-nano` (existing strip-eval winner; re-validated by
    new eval).
  - Research: `gpt-5-mini` with `web_search` (prototype-validated at
    ~$0.10/company).
- **Eval reshape, not rewrite.** Keep `eval/run.py` orchestrator shape
  (parallel workers, judge dispatch, CSV output) but rewrite work-item
  shape, judge prompts, and CSV columns for three-field scoring.

## Open Questions

### Resolved During Planning

- Migration strategy for existing `description_stripped` data → new
  column `description_normalized`, drop the old one in the same
  migration; re-populate via extract phase.
- Behavior of `get_job_post_details` for un-extracted jobs → return
  raw `description` with `extracted: false` marker.
- Default ordering for `search_jobs` when `query` empty → `published_at
  DESC NULLS LAST`.
- Pagination shape → cursor on `(published_at, company_slug, job_id)`.
- Refresh policy for `short_bio` → user-driven only;
  `jsb research-companies --stale 90d` is the documented invocation, no
  automatic cadence in Phase 1.
- Whether to add a research audit table → no; persist bio + timestamp;
  rely on logs for search trail.

### Deferred to Implementation

- Exact JSON schema field types for the extract structured-output call —
  needs a real call against the Azure deployment to confirm the schema
  is accepted (Azure occasionally rejects schemas that pass strict-mode
  validation locally).
- Whether `description_normalized` should be stored as `TEXT` or `JSONB`
  with a structured "removed sections" sidecar for debuggability —
  depends on whether the eval needs the diff. Default to `TEXT`; revisit
  if eval rubrics need it.
- Final bio length cap and word-count enforcement — the prompt asks for
  60–100 words but the model overshoots ~10% of the time on the
  prototype. Decide between prompt tuning vs. truncate-on-write.
- Exact set of companies for the bio cost-overhead investigation (Unit
  3b) — pick from the user's actual MCP search history, depends on
  current state of `query_embeddings_cache`-equivalent logs.

## High-Level Technical Design

> *This illustrates the intended pipeline shape and is directional guidance
> for review, not implementation specification. The implementing agent
> should treat it as context, not code to reproduce.*

```
                    ┌─────────────────────────────────────┐
   jsb sync         │  fetch  →  enrich  →  extract       │
   (zero paid       │  (HTTP)    (HTTP)     (LLM, no web)  │
   web search)      └─────────────────────────────────────┘
                              │
                              │  reads optional companies.short_bio
                              │  for <company_bio> context
                              ▼
                    jobs.short_jd
                    jobs.description_normalized
                    jobs.salary

   jsb research-companies    ┌─────────────────────────────┐
   (opt-in, paid)            │  research                   │
                             │  (Responses API + web_search)│
                             └─────────────────────────────┘
                                          │
                                          ▼
                             companies.short_bio
                             companies.research_updated_at

   MCP surface
     search_jobs(query, location, company, posted_since, include_company_bio)
        → rows [{title, location, short_jd, salary, posted_at, company_slug,
                 [optional: company_short_bio]}]
        → ranking: FTS rank when query, else published_at DESC

     get_job_post_details(company_slug, job_id)
        → {…, description: description_normalized OR raw, extracted: bool}

     get_company(slug)
        → {slug, name, ats, board, short_bio, research_updated_at}
```

The `<company_bio>` context flow is the load-bearing cross-pipeline link:
extract reads it as situational context (never restated in output);
research writes it. Decoupling means extract can run end-to-end with
empty bios on day one.

## Implementation Units

- [ ] **Unit 1: Schema migration — new fields, drop embedding stack**

**Goal:** Schema reflects the Phase 1 design: `short_jd`,
`description_normalized`, `short_bio`, `research_updated_at` exist;
embedding tables and indexes are gone; FTS column rebuilt.

**Requirements:** R1, R2, R3, R7, R8

**Dependencies:** None (foundation).

**Files:**
- Create: `src/jobbuddy/migrations/011_phase1_redesign.sql` (or split
  011a/011b/011c if pending-trigger constraints force it — see
  `MEMORY.md` §"migration_pending_triggers")
- Modify: `src/jobbuddy/migrations/__init__.py` (registry, if applicable)
- Test: `tests/test_store.py` (new fixtures verifying post-migration
  schema shape)

**Approach:**
- Add `short_jd TEXT` and rename `description_stripped` →
  `description_normalized` on `jobs`. Treat as drop+add of a new column
  if the rename causes pending-trigger issues; existing data is
  regenerable so loss is acceptable.
- Add `short_bio TEXT` and `research_updated_at TIMESTAMPTZ` on
  `companies`.
- Drop: `job_embeddings` table, its HNSW index, `query_embeddings_cache`
  table (010), and any indexes on the old strip column. Do not drop
  `pgvector` extension. Do not drop `companies` table.
- Rebuild `jobs_fts_vector` (009) over
  `coalesce(title,'') || coalesce(short_jd,'') ||
   coalesce(description_normalized,'')`. The trigger that maintains the
  column needs the new column list.
- Index plan: GIN on the rebuilt FTS column; B-tree on `(published_at
  DESC, company_slug, job_id)` for the default-ordering pagination.

**Patterns to follow:**
- `src/jobbuddy/migrations/009_fts_vector_column.sql` for FTS trigger
  shape.
- `src/jobbuddy/migrations/004_companies.sql` for `companies` column
  additions.

**Test scenarios:**
- Happy path: After `jsb migrate`, `jobs.short_jd` and
  `jobs.description_normalized` exist with `TEXT` type; `companies.
  short_bio` exists; `job_embeddings` table does not exist.
- Happy path: `jobs_fts_vector` regenerates correctly when a row's
  `short_jd` is updated (trigger fires).
- Edge case: Migration applied twice is a no-op (idempotency check via
  `schema_migrations`).
- Error path: Migration on a database with embedding rows present
  succeeds and drops them cleanly (reproduce by inserting fixture rows
  into `job_embeddings` before applying 011).
- Integration: Existing fixture jobs survive the migration with
  `description` (raw) intact and `description_normalized = NULL` until
  re-extracted.

**Verification:**
- `jsb migrate` against a copy of prod-shape data succeeds.
- `\d jobs` and `\d companies` show new columns; `\dt job_embeddings`
  errors.

---

- [ ] **Unit 2: `sync/extract.py` — three-field extract phase**

**Goal:** A new `WorkerPhase` that polls jobs missing `short_jd`,
calls the OpenAI-compatible API with `prompts/extract-v1.txt` and
structured output, writes `short_jd`, `description_normalized`, and
`salary` back. Replaces `sync/strip.py` in the orchestrator.

**Requirements:** R5, R9

**Dependencies:** Unit 1

**Files:**
- Create: `src/jobbuddy/sync/extract.py`
- Modify: `src/jobbuddy/sync/__init__.py` (register phase, update
  `SyncResult`, update `validate_sync_config` to require OpenAI key for
  `extract`)
- Modify: `src/jobbuddy/store.py` (new `list_jobs_needing_extract` /
  `update_job_extract` methods; deterministic ORDER BY to prevent the
  unordered-LIMIT race documented in `sync/embed.py`)
- Modify: `src/jobbuddy/types.py` (new `ExtractWorkItem`)
- Modify: `src/jobbuddy/cli/sync.py` (replace `strip` with `extract` in
  phase choices)
- Test: `tests/test_extract.py` (new), `tests/test_store.py` (extract
  query coverage)

**Approach:**
- Reuse `WorkerPhase` ABC verbatim. Threadpool workers, write queue,
  graceful shutdown — no new infrastructure.
- User-message construction wraps in XML tags exactly as the prompt
  expects: `<title>`, `<company>`, `<location>`,
  `<ats_provided_salary>`, `<company_bio>`, `<job_description>`. The
  `<company_bio>` value comes from a join against `companies.short_bio`
  (may be empty string).
- Structured output: `response_format={"type": "json_schema",
  "json_schema": {...strict...}}` with three required fields, `salary`
  nullable.
- "Done" predicate: `short_jd IS NOT NULL` (stable, no hash). Avoids the
  Azure embedding-starvation churn class.
- Failure mode: on parse error or API error, leave all three fields
  null; next sync retries. Same posture as `sync/strip.py`.
- Model defaults to `JOBBUDDY_EXTRACT_MODEL` (new setting), default
  `gpt-5-nano`.

**Execution note:** Implement test-first against a recorded structured-
output fixture; the prompt is large and spec-driven, so unit tests
should pin the JSON-schema contract and the XML-tag input shape before
wiring the live API.

**Patterns to follow:**
- `src/jobbuddy/sync/strip.py` — overall phase shape, threading,
  display state updates.
- `src/jobbuddy/sync/enrich.py` — secondary join read pattern (similar
  to how extract joins to `companies`).

**Test scenarios:**
- Happy path: Job with full inputs and present `company_bio` returns
  three fields populated and matching schema.
- Happy path: Job whose company has no bio sees `<company_bio></company_bio>`
  in the user message and still returns valid output.
- Edge case: `<ats_provided_salary>true</ats_provided_salary>` returns
  `salary: null` regardless of JD content.
- Edge case: JD with no salary mention and `ats_provided_salary=false`
  returns `salary: null`.
- Edge case: Very long JD (>50K chars) — current prompt has no fixed
  cap; verify model truncation behavior is graceful or raises.
- Error path: Schema-violating model output → row left null, retry on
  next sync.
- Error path: API timeout / rate-limit → row left null, error counted in
  `PhaseState`.
- Integration: Two-row fixture sync end-to-end; FTS index updates
  incorporate `short_jd` after extract completes.
- Integration: `list_jobs_needing_extract` is deterministic under
  concurrent worker polling (regression for the `sync/embed.py` race).

**Verification:**
- `uv run python -m pytest tests/test_extract.py -v` passes.
- `jsb sync extract --company anthropic` populates the three fields for
  fixture jobs.

---

- [ ] **Unit 3: `sync/research.py` core + auto-on-add wiring +
  `jsb research-companies` backfill CLI**

**Goal:** Company-bio research pipeline lands as one shared core
(`research_company(slug) → short_bio`) with two entry points:
(a) automatic invocation when a company is **explicitly** registered
via `jsb companies-add` / `jsb add-company`, and (b) a backfill CLI
that fills in any company missing a bio plus refreshes stale ones.
Implicit registration paths (URL-driven `register_company` calls in
`core.py`) enqueue the company for backfill rather than firing a paid
call inline.

**Requirements:** R6, R9

**Dependencies:** Unit 1

**Files:**
- Create: `src/jobbuddy/sync/research.py` — `research_company(slug)
  → ResearchResult` core function (single-company, sync, returns the
  bio + source trail; no DB writes itself), plus `run_research_batch
  (slugs, *, workers=4)` for the backfill CLI's parallelism.
- Create: `src/jobbuddy/cli/research.py` — `jsb research-companies`
  command.
- Modify: `src/jobbuddy/registry.py` — `register_company()` gains an
  optional `research: bool = False` kwarg. When true, calls
  `research_company` synchronously after the registry write and
  persists the result. When false (default for implicit registration),
  leaves `research_updated_at = NULL` so the company shows up in the
  backfill candidate set.
- Modify: `src/jobbuddy/cli/search.py` (`companies-add`) and
  `src/jobbuddy/cli/jobs.py` (`add-company`) — both pass
  `research=True` by default; add `--no-research` escape hatch for
  offline / batch use.
- Leave: `src/jobbuddy/core.py` URL-driven `register_company` calls
  (lines 104, 116, 164) **unchanged** — they pass `research=False`
  implicitly. URL flows must not surprise the user with paid calls.
- Modify: `src/jobbuddy/store.py` — new
  `list_companies_needing_research(stale_days: int | None)` /
  `update_company_research(slug, short_bio)`.
- Modify: `src/jobbuddy/openai_client.py` if needed for Responses-API
  factory (prototype uses `/openai/v1/` base URL, not the Chat
  Completions path).
- Test: `tests/test_research.py` (new); add a registry-integration
  test that confirms `jsb companies-add` populates the bio while
  URL-driven add does not.

**Approach:**
- Lift the prototype shape from
  `scratchpad/foundry_company_research.py` — AAD bearer auth,
  `client.responses.create`, `tools=[{"type": "web_search"}]`, strict
  JSON schema, `include=["web_search_call.action.sources"]` for
  source-trail logging.
- Backfill **and refresh are the same command**, differentiated by the
  candidate-selection flag. The CLI is `jsb research-companies` with:
  `--company NAME` (single; force, ignores freshness), `--all` (every
  company with `research_updated_at IS NULL` — the initial-backfill
  case), `--stale Nd` (refresh companies whose bio is older than N
  days — the future-refresh case), `--limit N`, `--dry-run` (count +
  estimated cost, no API spend), `--workers N` (default 4, well below
  the 1K RPM `gpt-5-mini` tier). `--all` and `--stale` may be combined
  to mean "everything missing or stale". This shape lets refresh
  evolve from a manual cadence into a scheduled cron job later without
  needing a new command surface.
- Cost reporting: end-of-run Rich table with per-company `web_search`
  count × $0.014 plus token usage. Keeps the spend visible even
  though the design no longer treats it as gating.
- Failure mode: per-company exceptions are caught and logged with the
  company slug; the rest of the run continues. Failed companies stay
  in the candidate set for next invocation. The auto-on-add path
  surfaces failures non-fatally — registration succeeds with
  `research_updated_at = NULL`, and the user sees a warning.
- The research **prompt** itself is being tuned in a parallel session;
  this unit consumes whatever version is at
  `prompts/company-research-v1.txt` at merge time. Prompt path is
  configurable via `JOBBUDDY_RESEARCH_PROMPT_VERSION`.

**Execution note:** Test-first against a recorded Responses-API
fixture so the call shape (tools, schema, includes) is pinned before
wiring the live API.

**Patterns to follow:**
- `scratchpad/foundry_company_research.py` — canonical reference.
  Production pipeline differs only in: writes to DB rather than files,
  worker pool for batch, cost summary, integration with the registry
  add path.

**Test scenarios:**
- Happy path: `jsb companies-add "Acme Robotics" --ats greenhouse
  --board acmerobotics` populates `companies.short_bio` synchronously.
- Happy path: `register_company("Acme")` called from a URL parser
  (`core.py`) leaves `short_bio` and `research_updated_at` null.
- Happy path: `jsb research-companies --all` picks up exactly the
  companies registered without research and produces bios for each.
- Happy path: `jsb research-companies --stale 90d` selects only
  companies older than 90 days.
- Edge case: `--dry-run` issues zero API calls and reports the
  candidate count + estimated cost.
- Edge case: `companies-add --no-research` registers without a bio;
  the company surfaces in the next backfill.
- Edge case: Responses-API returns `output_text` that fails JSON parse
  → row not updated, error logged.
- Error path: Auto-on-add research call fails (network, AAD, schema
  violation) → registration still succeeds; warning emitted; backfill
  picks it up later.
- Error path: AAD token expired mid-batch → request retries with
  refresh, not silent corruption.
- NPOV: Output bio passes the banned-phrasings regex check
  ("fast-paced", "scrappy", "world-class", trailing meta-line about
  who the role suits).

**Verification:**
- `jsb companies-add` populates a bio inline.
- `jsb research-companies --all` clears the no-bio backlog.
- `jsb research-companies --stale 90d --dry-run` reports candidates
  with no API spend.
- `jsb lookup <new-url>` registers an unknown company without firing
  a paid call.

---

- [ ] **Unit 4: MCP tool surface — extend `search_jobs`, swap
  `get_job_post_details`, add `get_company`**

**Goal:** MCP consumers see the new behavior. `search_jobs` returns
`short_jd` inline and supports the new filters. `get_company` exists.

**Requirements:** R1, R2, R3, R4

**Dependencies:** Unit 1, Unit 2 (so the new fields exist and are
populated for at least some rows)

**Files:**
- Modify: `src/jobbuddy/mcp_server.py`
- Modify: `src/jobbuddy/core.py` (new query helpers if needed; keep
  Typer/FastMCP deps out)
- Modify: `src/jobbuddy/store.py` (new `search_jobs_fts` /
  `get_company` methods)
- Modify: `src/jobbuddy/cli/search.py` (CLI parity — `jsb search` gains
  `--query`, `--posted-since`)
- Test: `tests/test_search.py` (rewrite — old `VectorSearch` tests will
  be deleted in Unit 6)

**Approach:**
- `search_jobs` parameters: `title`, `location`, `company`,
  `posted_since` (parser accepts `"7d"`, `"2w"`, `"1mo"`, ISO date,
  ISO datetime), `query` (FTS via `to_tsquery` over the new
  `jobs_fts_vector`), `include_company_bio` (bool, default false),
  `limit` (default 20, cap 100), `cursor` (opaque base64 of
  `(published_at, company_slug, job_id)`).
- Ranking: when `query` is non-empty, `ts_rank` over FTS plus tie-break
  on `published_at DESC`. When `query` is empty, pure
  `published_at DESC NULLS LAST`. Drop any vestige of embedding
  similarity.
- Row shape: `{company_slug, company_name, job_id, title, location,
  short_jd, salary, posted_at, apply_url}` plus
  `company_short_bio: str | None` only when
  `include_company_bio=true`.
- `get_job_post_details` now reads `description_normalized` when
  present, raw `description` otherwise; returns `extracted: bool`.
- `get_company(slug)` reads from `companies` and returns
  `{slug, name, ats, board, short_bio, research_updated_at}`. Returns
  a structured "no bio yet" marker, not an empty string, when
  research has not run.

**Execution note:** Test-first against the new `core.py` helpers; MCP
tool functions stay thin wrappers.

**Patterns to follow:**
- Existing `search_jobs` in `src/jobbuddy/mcp_server.py` for
  parameter-doc style.
- `src/jobbuddy/cli/search.py` for `--filter` parsing patterns.

**Test scenarios:**
- Happy path: `search_jobs(query="rust", posted_since="14d")` returns
  ranked rows with `short_jd` inline, all within posted-since window.
- Happy path: `search_jobs(company="anthropic")` returns rows ordered
  by `published_at DESC`.
- Happy path: `search_jobs(include_company_bio=true)` includes
  non-null `company_short_bio` for companies that have one.
- Edge case: `posted_since="invalid"` → 400-style error from `core.py`
  with a descriptive message; MCP returns the message string.
- Edge case: Job with `description_normalized=NULL` → `get_job_post_
  details` returns raw `description` and `extracted: false`.
- Edge case: `get_company` for a slug that has never been researched
  returns `short_bio: null` and `research_updated_at: null`, not 404.
- Error path: Postgres connection drop mid-query → exception bubbles to
  MCP, which returns an error string per existing convention.
- Integration: Cursor pagination — page 1 + page 2 return disjoint
  rows under inserts that happen between pages (cursor stability
  test).
- Integration: FTS query updates incorporate a freshly extracted job
  within one sync cycle.

**Verification:**
- `jsb-mcp` smoke test from Claude Desktop: `search_jobs(query="ml")`
  returns rows with `short_jd`, `salary`, `posted_at`.
- `jsb search --query ml --posted-since 7d` (CLI parity) shows the
  same data.

---

- [ ] **Unit 5: MCP tool descriptions + server instructions —
  prompt-engineering pass**

**Goal:** Tool descriptions and server-level `instructions` push the
calling LLM toward the new shape: use `query` + `posted_since`, prefer
inline `short_jd` over re-fetching, use `get_company` for vibe context
on demand. Single-call coverage of `include_company_bio` semantics so
the calling LLM doesn't accidentally inflate every search.

**Requirements:** R1, R3, R4

**Dependencies:** Unit 4

**Files:**
- Modify: `src/jobbuddy/mcp_server.py` (`@mcp.tool` docstrings, field
  `description`s, server `instructions=`)
- Test: manual eyeball via Claude Desktop; no automated test.

**Approach:**
- Server `instructions`: emphasize "this is a fact-dense data provider;
  the calling LLM is the ranker. Returned rows are not for direct user
  display." Trigger phrases: "find jobs at", "PM jobs at", "remote ML
  roles", "what about this one", etc.
- `search_jobs` docstring: lead with *when to use* (free-text search
  across cached jobs); call out that `query` is fuzzy text not embedding
  search; document `posted_since` examples.
- `get_company` docstring: "fetch vibe context for a single company; use
  this instead of `include_company_bio=true` when you only need bios for
  a couple of companies."
- Field `description`s: examples for `posted_since`, valid values for
  `query`, default-off note for `include_company_bio` with a guidance
  sentence on when to flip it.

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

- [ ] **Unit 6: Eval harness — three-field extract scoring + bio
  scoring**

**Goal:** The eval harness scores the new extract pipeline (three
outputs, each with its own rubric) and the company-research pipeline
(short_bio NPOV/factuality). Strip-prompt-specific content is
deleted; harness shape and parallelism remain.

**Requirements:** R10

**Dependencies:** Unit 2, Unit 3 (need real outputs to score)

**Files:**
- Modify: `eval/run.py` (work-item shape, output CSV columns,
  multi-output dispatch)
- Modify: `eval/judge.py` (new judge prompts; multi-rubric scoring per
  job)
- Create: `eval/prompts/extract-judge-v1.txt`
- Create: `eval/prompts/research-judge-v1.txt`
- Delete: `eval/prompts/v1-` through `v9-` strip prompts (all)
- Delete: any strip-specific config files
- Modify: `eval/AGENTS.md` (update workflow to reflect new artifacts)
- Test: `tests/test_eval_runner.py` if one exists; otherwise smoke-test
  by hand.

**Approach:**
- `run.py` work item now wraps a `Job` and produces three judge
  invocations (one per output field) plus a fourth bio judge when the
  company has been researched. Judge calls remain parallel.
- CSV columns: `job_id, model, prompt_version, short_jd_recall,
  short_jd_precision, short_jd_npov, jd_norm_recall, jd_norm_precision,
  jd_norm_integrity, jd_norm_fidelity, salary_correctness,
  bio_factuality, bio_npov, bio_length_compliance, ...`.
- Judge prompts: rewritten from scratch, multi-dimensional, with score
  anchors per `MEMORY.md` §"Eval Lessons Learned". Use
  DeepSeek-R1-0528 as the default judge model (don't grade
  gpt-5-nano output with another gpt-5-* judge).
- Reuse the 75-worker default; respect the 1K RPM tier caps for
  judges.

**Patterns to follow:**
- Existing `eval/run.py` for orchestrator shape and accumulator
  pattern (the deferred smell #4 cleanup is *not* part of this unit;
  do it separately if it gets in the way).
- `MEMORY.md` §"Strip Prompt Eval: Conclusions" for the score-anchor
  approach.

**Test scenarios:**
- Happy path: A 5-job eval run produces a complete CSV with all rubric
  columns populated.
- Edge case: Job with `salary IS NULL` and `ats_provided_salary=true`
  → `salary_correctness` rubric correctly grades null as correct.
- Edge case: Company with no bio → bio rubric columns are empty/N/A,
  not zero (don't pollute aggregates).
- Integration: Run on 30 jobs across 3 companies, baseline current
  `prompts/extract-v1.txt`. Use this as the seed for Unit 7 prompt
  iteration.

**Verification:**
- `jsb-eval --jobs 30 --model gpt-5-nano` runs end-to-end, produces a
  CSV.
- Aggregates published as a markdown table (current eval pattern).

---

- [ ] **Unit 7: Extract prompt tuning — extract-v1 → vN**

**Goal:** Iterate `prompts/extract-v1.txt` against the new extract
eval until scores converge. The user explicitly flagged this: "we'll
need some prompt tuning for the JD."

**Out of scope:** `prompts/company-research-v1.txt` is being tuned
in a parallel session and is not part of this unit. When that session's
result lands, this plan's Unit 3 picks it up by reading whatever
version is on disk.

**Requirements:** R5, R9, R10

**Dependencies:** Unit 6

**Files:**
- Create: `prompts/extract-v2.txt`, `-v3.txt`, … as iteration
  proceeds. Keep all versions in version control so eval comparisons
  stay reproducible.
- Modify: `prompts/extract-v1.txt` only to fix typos; otherwise treat
  as immutable baseline.
- Modify: `src/jobbuddy/sync/extract.py` to read whichever version is
  configured via `JOBBUDDY_EXTRACT_PROMPT_VERSION` (default to the
  most recent winner).
- No tests — eval is the test.

**Approach:**
- Run baseline eval on `extract-v1.txt`. Identify the lowest-scoring
  rubric.
- For each iteration: change one thing (one rubric, one principle).
  Re-run on the same fixture set. Compare. Promote if ≥0 change in
  every rubric and >0 in the targeted one.
- Apply the lessons from `MEMORY.md` §"Strip Prompt Eval: Conclusions":
  fixing one outlier often creates another with `gpt-5-nano`. Stop
  when net improvement plateaus, not when one rubric is "perfect."

**Test scenarios:** Eval *is* the scenario set. No additional tests.

**Verification:**
- Selected prompt version's eval scores are stored in
  `eval/results/`.
- The chosen version is documented in `prompts/CHANGELOG.md` (new
  file) with the eval CSV path that justified the choice.

---

- [ ] **Unit 8: Remove dead embedding stack**

**Goal:** Code, tests, and CLI commands tied to job embeddings are
gone. The repository is smaller and clearer.

**Requirements:** R7

**Dependencies:** Unit 4 (MCP no longer references the old code), Unit
6 (eval no longer references the old code)

**Files:**
- Delete: `src/jobbuddy/sync/strip.py`
- Delete: `src/jobbuddy/sync/embed.py`
- Delete: `src/jobbuddy/embeddings.py`
- Delete: `src/jobbuddy/search.py` (`VectorSearch`)
- Delete: `src/jobbuddy/cli/generate_embed_text.py` (if present;
  `embed-test` command)
- Delete: `tests/test_embeddings.py`
- Delete: `tests/test_search.py` *only after* Unit 4 has its own
  replacement test file
- Modify: `src/jobbuddy/sync/__init__.py` (drop strip/embed phase
  registration; `validate_sync_config` no longer requires OpenAI key
  for embeddings)
- Modify: `src/jobbuddy/cli/sync.py` (remove `strip`/`embed` phase
  choices, surface `extract` instead — most of this lands in Unit 2)
- Modify: `src/jobbuddy/settings.py` (remove `embedding_model`,
  `strip_model`, `strip_batch_size` if no longer referenced)
- Modify: `pyproject.toml` if removing dependencies (`pgvector`'s
  Python client may be deletable; the SQL extension stays).
- Modify: `AGENTS.md` (update file map and sync-pipeline section)

**Approach:**
- Rip out, don't deprecate. The brainstorm chose a clean cut. Backwards-
  compat shims would only complicate the new path.
- Verify nothing else imports the deleted modules: `rg "from
  jobbuddy.embeddings|from jobbuddy.search|VectorSearch"`.
- Update `AGENTS.md` package structure section to match.

**Test scenarios:**
- Happy path: `uv run python -m pytest tests/ -v` passes after
  deletions.
- Happy path: `jsb --help` no longer lists `embed-test`; `jsb sync
  --help` no longer lists `strip`/`embed`.
- Edge case: `jsb sync` (no phases) runs `fetch enrich extract` and
  exits clean with zero embedding-related warnings.

**Verification:**
- `rg "embedding|VectorSearch|short_jd"` shows hits only in expected
  places (settings, extract, store, MCP).
- Final commit diff is net-negative LOC.

## System-Wide Impact

- **Interaction graph:** Extract phase joins to `companies.short_bio`
  during user-message construction. Research phase writes to
  `companies` only. MCP `search_jobs` and `get_job_post_details` shift
  from reading `description_stripped` to reading `description_normalized`
  and `short_jd`. FTS trigger re-fires on every `UPDATE` of `short_jd`
  or `description_normalized`.
- **Error propagation:** Extract failures leave row fields null;
  research failures leave company `short_bio` null; both retry on next
  invocation. MCP reads tolerate nulls (degraded but not broken).
- **State lifecycle risks:** During the extract backfill window,
  `search_jobs` is partial — rows with `short_jd IS NULL` will not
  match `query` searches over their JD. The `posted_since` and
  `company` filters still work. Acceptable trade per Key Decisions.
- **API surface parity:** `jsb search` CLI and `search_jobs` MCP tool
  must stay in lockstep on the new filters — both sit on top of the
  same `core.py` helpers.
- **Integration coverage:** Tests must cover (a) extract write →
  trigger → FTS update → search match, (b) research write → extract
  read of new bio context on next run, (c) MCP cursor stability under
  concurrent inserts.
- **Unchanged invariants:**
  - Raw `description` column is preserved on every job. The brainstorm
    explicitly keeps it as the audit / re-extraction source.
  - `lookup_by_name` and `companies` MCP tools are unchanged.
  - `log_job_application` and `log_job_activity` are untouched.
  - `pgvector` extension stays installed; `companies` table stays.
  - Existing fetcher contracts and ATS integrations are untouched.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Extract pipeline produces lower-quality `short_jd` than the prototype on edge-case JDs (multi-role postings, very short JDs, non-English JDs) | Eval harness covers this in Unit 6; prompt iteration in Unit 7. Worst case: ship with `extract-v1` and iterate post-launch. |
| Search is empty for un-extracted jobs during backfill window | Backfill before flipping the MCP tool descriptions to advertise `query`. Sequence: Unit 2 runs to completion on prod data, then Unit 5 lands. |
| Implicit company registration via URL parsing (`core.py`) accidentally fires paid `web_search` calls | Auto-on-add applies only to the explicit `companies-add` / `add-company` CLIs. `register_company` from URL parsers passes `research=False`. `jsb research-companies --all` is the path that picks up URL-discovered companies. |
| Migration 011 trips PG pending-trigger constraint (per `MEMORY.md`) | Pre-split into 011a/011b/011c if needed; rehearse on the dev DB. |
| Calling LLM ignores `short_jd` and keeps re-fetching JDs | Unit 5's tool-description prompt-engineering is the lever. Verify behavior in Claude Desktop before declaring Unit 5 done. |
| Removing embeddings while resume project depends on jobsearch-buddy as editable dep breaks resume's MCP usage | Resume project consumes `jsb-mcp` and `jsb` CLI, both of which are reshaped in this plan. Smoke-test resume's flows after Unit 4 + Unit 8. |
| `description_stripped` rename loses search-relevant content for jobs that won't be re-extracted (e.g., closed listings) | Closed listings are out of search scope anyway; if needed, populate `description_normalized = description_stripped` as a one-time SQL backfill in 011 to soften the cut. Decide at migration-write time. |

## Documentation / Operational Notes

- Update `AGENTS.md`:
  - Package structure section (no `strip.py`, `embed.py`,
    `embeddings.py`, `search.py`; new `extract.py`, `research.py`)
  - Sync pipeline section (three phases not four; research is
    separate)
  - Schema migrations section (note the 011 split if it happens)
- Update `docs/architecture.md`:
  - Strip → Extract pipeline shape diagram
  - Removal of vector search; replacement with FTS
  - Company-research pipeline as a separate, opt-in track
- Update `docs/throughput-reference.md` with new extract throughput
  numbers (likely similar to strip; confirm post-launch).
- Add `prompts/CHANGELOG.md` to track prompt iterations.
- `MEMORY.md`: After Phase 1 ships, record the cost-overhead
  investigation outcome (Unit 3b) as a topic file. Resolve the
  Azure embedding starvation entry — embeddings are gone.
- Operational rollout:
  1. Apply migration 011 in dev → prod after rehearsal.
  2. Run `jsb sync extract` to backfill `short_jd` and
     `description_normalized` for all jobs.
  3. Run `jsb research-companies --all` to backfill bios across the
     existing registry. New companies arrive with bios automatically
     from this point.
  4. Deploy MCP changes (Unit 4 + Unit 5).
  5. Remove dead code (Unit 8) once consumers (resume project) are
     verified.
  6. (Future, not Phase 1) Schedule `jsb research-companies --stale Nd`
     as a refresh cron once a refresh cadence is chosen.

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
- Prototype: `scratchpad/foundry_company_research.py` and
  `scratchpad/runs/` (validated Azure Responses API + `web_search`
  shape and cost)
- Prompts already in repo: `prompts/extract-v1.txt`,
  `prompts/company-research-v1.txt`, `prompts/embed-text-v1.txt`
  (Phase 2 only)
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
