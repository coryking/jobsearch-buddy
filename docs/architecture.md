# Architecture

## Overview

jobsearch-buddy has two interfaces sharing a single core:

- **CLI** (`jsb` command, Typer) — interactive use, rich output
- **MCP server** (`jsb-mcp`, FastMCP) — Claude Desktop integration

Both call into `core.py`. Core raises `ValueError`; callers handle presentation.

Phase 1 of the search redesign replaced the strip+embed pipeline with a
single LLM **distill** phase that produces three derived fields per job
(`short_jd`, `description_normalized`, `salary`). Search is PostgreSQL
full-text search over those fields plus title/location/department.

Phase 2 (company-side) is partly merged: the `ResearchPhase` populates
`companies.short_bio` and `long_bio` via Azure Responses API + web_search.
`long_bio` feeds the distill prompt as `<company_bio>` context. The
`pgvector` extension stays installed but no current code uses it.

## Data Access — JobStore

`store.py` provides the `JobStore` class — a PostgreSQL data access layer
with surrogate keys. All DB access goes through JobStore; no raw `conn`
passing. Each `JobStore` instance owns a single `psycopg` connection;
worker threads in the sync pipeline each create their own instance.

| Operation | Source | Notes |
|-----------|--------|-------|
| `search_jobs` MCP | JobStore | FTS over title/short_jd/description_normalized/location/department |
| `get_job_post_details` MCP | JobStore + live fallback | Returns `description_normalized` if distilled, else raw `description` |
| `jsb list-jobs` CLI | JobStore | Optional company filter |
| `jsb search` CLI | JobStore | Title/location/company filters |
| `jsb sync` CLI | Live API + LLM | Populates/refreshes cache, runs distill |
| `log_job_application` MCP | Live API | Saves listing as markdown |
| `jsb lookup` CLI | Live API | Single-job detail fetch |

### Schema

```sql
jobs             -- SERIAL PK (surrogate), UNIQUE(company_slug, job_id)
                 --   title, location, url, published_at,
                 --   last_listing_update (per-fetcher freshness signal — see
                 --                        "Date columns" below), salary, team,
                 --   department, description (raw, never returned by MCP),
                 --   short_jd, description_normalized (distill outputs),
                 --   ats_metadata (JSONB), last_seen,
                 --   listing_status (enum: active/removed), removed_at,
                 --   fts_vector (generated tsvector, A=title, B=short_jd,
                 --              B=description_normalized, C=location,
                 --              D=department)
companies        -- slug PK, name, ats, board, config (JSONB)
sync_status      -- per-company last sync time and error state
schema_migrations -- applied migration filenames
activity_log     -- application/contact tracking (CSV-shaped rows)

-- Indexes
idx_jobs_fts_vector             -- GIN on fts_vector
idx_jobs_published_pagination   -- (published_at DESC NULLS LAST, company_slug, job_id)
idx_jobs_needs_distill          -- partial: WHERE description IS NOT NULL AND short_jd IS NULL
                                --          AND listing_status = 'active'
idx_jobs_active                 -- partial: WHERE listing_status = 'active'
idx_jobs_company, idx_jobs_published
```

The raw `description` is preserved on every job — it's the audit /
re-distill source. MCP `get_job_post_details` returns
`description_normalized` if present, raw `description` otherwise, with a
`distilled: bool` marker so consumers can tell.

When the distill phase writes `short_jd` / `description_normalized`, the
`fts_vector` generated column updates automatically. When `upsert_jobs`
sees the raw `description` change on an existing job, it nulls
`short_jd` / `description_normalized` so the distill phase picks the
row up again on the next pass — replaces the content_hash invalidation
path that the embedding pipeline used.

### Date columns

Two date columns capture different ATS-side semantics:

- `published_at` — what the ATS reports as the listing's publish date.
  Source field varies: Greenhouse `first_published`, Lever `createdAt`,
  Ashby `publishedAt`, Apple `postDateInGMT`, Eightfold `postedTs`,
  Avature sitemap `<lastmod>`, etc. Pure-insert: fixed at first sync.
- `last_listing_update` — what the ATS reports about freshness, where a
  distinct field exists. Updates on every sync via `GREATEST()`, so a
  newer ATS-side timestamp overrides the stored value while a NULL never
  clobbers an existing one.
- `effective_date` — generated `COALESCE(last_listing_update,
  published_at)`. Drives `posted_after` filtering and recency ordering in
  `query_jobs` / `search_jobs_fts` / `survey_jobs_by_company` so "posted
  in the last 30 days" surfaces ATS-fresh listings rather than only those
  whose first-publish date happens to be recent. `published_at` is still
  what surfaces in `CompactJob.published_at` and what users see on the
  detail page; `last_listing_update` is exposed alongside it as
  `updated` so the LLM can see the divergence.

Per-fetcher mapping for `last_listing_update`:

| Fetcher       | Source field                                              |
|---------------|-----------------------------------------------------------|
| greenhouse    | `updated_at` (board API)                                  |
| amazon        | `updatedDate` (search hit fields)                         |
| eightfold_v2  | `t_update` (epoch seconds on each position)               |
| jibe          | `update_date`, falling back to `meta_data.icims.date_updated` |
| avature       | sitemap `<lastmod>` (also feeds `published_at` — Avature has no separate first-publish field) |
| (others)      | NULL — public API exposes only one date                   |

Live probes against every wired ATS confirm the table above is exhaustive
for ATSes that publicly expose a separate "last updated" timestamp. See
issue #53 for the per-platform investigation.

### Soft-Delete

When a job disappears from a company's feed, its `listing_status` is set
to `'removed'`. A database trigger auto-manages `removed_at` — setting
it on removal and clearing it on reactivation. Jobs that reappear get
`listing_status = 'active'` and `removed_at = NULL`. `query_jobs()`
excludes removed jobs by default. Re-postings (removed → active) are
logged at INFO.

## Search

`store.query_jobs()` is the FTS entry point. The `title` parameter is
fed to `websearch_to_tsquery('english', ...)` against `fts_vector`,
which weights title highest, then short_jd + description_normalized,
then location, then department.

When a query is set, ranking is `ts_rank(fts_vector, q) DESC` with
`published_at DESC NULLS LAST` as tie-break. When the query is empty,
pure `published_at DESC NULLS LAST`. No diversity cap, no cursor in
Phase 1 — SERP tuning is a deferred follow-up. The premise is that the
calling LLM (Claude Desktop, ChatGPT) issues keyword queries and does
its own semantic reasoning over the returned `short_jd` rows. Result
ranking is a tuning layer on top of correct FTS, not a Phase 1
correctness concern.

## Sync Pipeline

`sync/` package orchestrates three phases:

1. **FetchPhase** (`sync/fetch.py`): Parallel company fetching via
   ThreadPoolExecutor. Soft-deletes jobs that disappeared from the
   feed; reactivates jobs that came back.
2. **EnrichPhase** (`sync/enrich.py`): Description enrichment for stub
   fetchers (Workday, Eightfold, Oracle HCM, etc.) that don't return
   descriptions in bulk listings.
3. **DistillPhase** (`sync/distill.py`, *Unit 2*): One structured-output
   LLM call per job; produces `short_jd`, `description_normalized`,
   and `salary` in a single round trip. Replaces the old strip+embed
   stack.

Distill requires OpenAI credentials — either `JOBBUDDY_OPENAI_API_KEY`
(local) or `JOBBUDDY_OPENAI_AZURE_API_VERSION` with managed identity
(Azure). Sync fails fast at startup if neither is configured. Use
`jsb sync fetch enrich` to run without OpenAI credentials.

### DB-as-Queue Pattern

`EnrichPhase` and `DistillPhase` extend the `WorkerPhase` ABC
(`sync/base.py`). Each polls the database for unprocessed work items,
processes them in a `ThreadPoolExecutor`, and writes results back
through a single-threaded `WriteQueue`. The database is the
coordination mechanism — no in-memory queues between phases.

`WorkerPhase` provides:
- `count_remaining()` / `poll_work(batch_size)` / `process_item(item)`
  — abstract methods subclasses implement
- `ThreadPoolExecutor` with configurable `max_workers`
- Single-threaded `WriteQueue` for all DB writes
- Graceful shutdown via `threading.Event`
- `PhaseState` display updates on advance/error

The distill phase's "needs work" predicate is a stable column-presence
check (`short_jd IS NULL AND description IS NOT NULL`) — no hash. The
upsert nulls `short_jd`/`description_normalized` when raw description
changes, so the distill phase picks the row up again on the next pass.

### Logging and progress

Sync output is stdlib `logging` to stderr with `asctime` timestamps. No
Rich Live TUI. `PhaseState` (`sync/display.py`) is a thread-safe metrics
struct: workers update `done`, `errors`, `info_tokens`, `active_workers`
directly (GIL-atomic); `RollingRate` / `RollingTokenRate` track per-minute
rates from a 60-second sliding window. A `HeartbeatLogger`
(`sync/heartbeat.py`) thread samples each PhaseState every 30s by default
and emits one key=value INFO line per active phase, e.g. `phase=Distill
status=active done=142 total=500 pct=28 rate=18/m tok_rate=4.2k/m
info=85.3k_tok cached=87% workers=3/5 errors=2`. `-v/--verbose` enables
per-item DEBUG logs. `--heartbeat 0` disables periodic emission for tight
cron loops.

WriteQueue failures are fatal: on any non-recoverable write error (or a
retry that still fails after reconnect) the queue calls `_bail()`,
which logs the traceback, drains pending items without executing, and
re-raises on the next `submit()` / `flush()`. The exception propagates
out of the phase and crashes the sync non-zero. There is no silent-drop
path — the upstream LLM/HTTP call producing the row was already paid for.

### Sync Concurrency

Fetch uses `ThreadPoolExecutor(max_workers)`. Companies are shuffled
before sync to spread same-platform requests (rate-limit mitigation).
Each company is error-isolated. Distill runs ~30 workers (RPM-bound).
Enrich runs ~5 workers, sequential per-company for rate-limit
mitigation.

## Fetcher Architecture

Strategy pattern. Each ATS platform is a class inheriting `ATSFetcher`
(in `fetchers/base.py`). Board slug is instance state.

Factory functions in `fetchers/__init__.py`:
- `get_fetcher(slug)` — looks up company in registry, returns
  configured instance
- `create_fetcher(ats_type, board=..., **kw)` — low-level, explicit
  params

`list_jobs()` fetches discovery metadata (may include descriptions).
`fetch_job()` fetches a single job's full details.

**Stub vs Full fetchers:** Workday, Eightfold, Oracle HCM don't return
descriptions in bulk listings. After fetch, the enrich phase calls
`fetch_descriptions()` for stub-fetcher jobs that lack descriptions.

## Settings

`settings.py` uses pydantic-settings. Priority: env vars > defaults.
See `CLAUDE.md` for the full settings table.

OpenAI credentials are required for the distill phase. The `pgvector`
extension stays installed but no Python client uses it (Phase 2 will).

## Company Registry

`registry.py` provides company lookup. Companies live in PostgreSQL's
`companies` table; `companies.json` (legacy) is no longer authoritative.

Fuzzy lookup: `lookup_by_name()` tries exact match on name/slug first,
then normalized alphanumeric match (strips spaces, case, special
chars). Also resolves the ATS board identifier when the input matches
a board slug.

## Saved Listings Format

`{listings_dir}/{slug}/{YYYY-MM-DD}_{slugified-title}_{job_id}.md`

Markdown structure: `# Title`, `## Essentials` (metadata bullets),
`## Description` (plaintext, HTML stripped).

## MCP Tool Descriptions

Tool descriptions and field descriptions in `mcp_server.py` are
injected into the LLM's context. Write them as routing hints, not API
docs:

- **Server `instructions`**: Intent language that matches natural
  queries. Name specific companies. Bias toward trying the tool first.
- **Tool docstrings**: Lead with *when to use*, not *what it does*.
- **Field `description`s**: Format hints, examples, valid values.
  Dense, not verbose.
