---
description: Sync pipeline internals — phases, the WorkerPhase ABC, DB-as-queue pattern, distill predicate, display
globs: src/jobbuddy/sync/**/*.py
---

# Sync Pipeline

**Status: dormant.** The MCP surface serves stateless live fetches and the
deploy keeps `jsb-sync.timer` disabled, so nothing runs this pipeline on a
schedule. The machinery remains fully functional via `jsb sync` and this rule
stays authoritative for anyone touching `src/jobbuddy/sync/`.

The sync pipeline uses a **DB-as-queue** pattern. Four phases are wired:

1. **Fetch** — parallel company fetching via ThreadPoolExecutor
2. **Enrich** — description enrichment for stub fetchers (Workday, Eightfold, etc.)
3. **Research** — Azure Responses API + web_search to fill `companies.short_bio`
   and `long_bio`, then immediately embed `long_bio` with
   `text-embedding-3-small` into `bio_embedding vector(1536)` for the
   `find_companies` MCP tool. Bio + embedding are paired in one step so a
   research run leaves the company queryable by find_companies. Independent
   of the job pipeline (polls companies, not jobs). Requires both
   `JOBBUDDY_RESEARCH_ENDPOINT` (Azure Responses) and an OpenAI key
   (`JOBBUDDY_OPENAI_API_KEY` / `JOBBUDDY_OPENAI_AZURE_API_VERSION`) —
   the latter is used by the inline embedding call.
4. **Distill** — one structured-output LLM call per job, producing `short_jd`,
   `description_normalized`, and `salary` in a single round trip. Polls the
   `idx_jobs_needs_distill` predicate (`description IS NOT NULL AND short_jd
   IS NULL AND listing_status = 'active'`).

`jsb sync` runs all wired phases by default. Distill runs sequentially after
enrich+research join — it depends on description (from enrich) and reads
`companies.long_bio` (from research) at `on_phase_start()`. Research requires
an Azure Responses-API endpoint (`JOBBUDDY_RESEARCH_ENDPOINT` or
`JOBBUDDY_OPENAI_BASE_URL`) with bearer-token auth via managed identity.
Distill requires OpenAI credentials (`JOBBUDDY_OPENAI_API_KEY` or
`JOBBUDDY_OPENAI_AZURE_API_VERSION` with managed identity). Sync fails fast
at startup if a selected phase's credentials are missing.

**Backfill ordering:** research must run before distill — distill consumes
`long_bio` as `<company_bio>` context. Run `jsb research-companies` first,
then `jsb sync distill`.

Preconditions (phase names, OpenAI key, company resolution) are validated
up front by `validate_sync_config()` before any I/O. The orchestrator
(`sync_jobs()`) trusts the caller and does not re-validate.

All phases update `PhaseState` objects directly for display — no event queue.

## WorkerPhase ABC

`EnrichPhase`, `ResearchPhase`, and `DistillPhase` extend the
`WorkerPhase` ABC (`sync/base.py`), which provides: DB polling for work
items, `ThreadPoolExecutor` parallelism, DB writes via a single-threaded
`WriteQueue`, graceful shutdown via `threading.Event`, and display state
updates. Phases poll the database for unprocessed items, process them in
worker threads, and write results back. This decouples phases — each can
run independently via phase selection (`jsb sync research`,
`jsb research-companies`).

## Distill predicate

The distill phase's "needs work" predicate (already enforced by the
`idx_jobs_needs_distill` partial index) is a stable column-presence check
(`short_jd IS NULL AND description IS NOT NULL AND listing_status = 'active'`)
— no hash. Note the upsert is pure-insert for content columns: a changed
`description` on the ATS side does NOT update the stored row and does NOT
re-trigger distill. Stored content reflects first capture.

## Logging and progress

Sync output is stdlib `logging` to stderr with `asctime` timestamps —
greppable, journalctl-friendly, LLM-friendly. There is no Rich Live TUI.

`PhaseState` (`sync/display.py`) is now a pure metrics struct: phase
workers update `done`, `errors`, `info_tokens`, `active_workers`, etc.
directly (GIL-atomic) and `RollingRate` / `RollingTokenRate` track
items-per-minute from a 60-second sliding window. A `HeartbeatLogger`
(`sync/heartbeat.py`) thread samples each PhaseState every 30s and emits
one structured INFO line per active phase, e.g. `phase=Distill
status=active done=142 total=500 pct=28 rate=18/m tok_rate=4.2k/m
info=85.3k_tok cached=87% workers=3/5 errors=2`. Cadence is wall-clock
so a stalled phase is visibly stalled rather than silently quiet.
`-v/--verbose` drops jobbuddy loggers to DEBUG for per-item activity.
`--heartbeat 0` disables the heartbeat (e.g. tight cron loops).

## WriteQueue failure policy

Per-row write failures are fatal, never silent. On a connection-dead
error WriteQueue closes the dead connection, reopens via
`conninfo_factory` (refreshes the Entra token in Azure mode), and
retries the failing callable once. Reconnect failure, retry failure, or
any non-connection write error → `_bail()`: log.error with traceback,
mark queue fatal, drain pending items without executing, set
`self._fatal`. The next `submit()` / `flush()` re-raises, propagates out
of the phase's `run()`, and crashes the sync with a non-zero exit. The
upstream LLM/HTTP call that produced the row was already paid for, so
silently dropping the row would mean burning money for nothing AND
leaving stale NULLs that look identical to "never processed".

Ordering invariants that keep "fatal" honest (all pinned by
`tests/test_worker_phase_fatal.py`):

- The writer publishes `_fatal` **before** `task_done()`, so the first
  `flush()` after a poison write sees it rather than racing past.
- `run()` sends worker sentinels in a `finally` — a producer-loop
  exception must terminate the phase, not strand workers on
  `work_queue.get()` and deadlock the executor join.
- Workers check `WriteQueue.is_fatal` **before** `process_item()`. The
  paid LLM call happens inside `process_item`; once the write path is
  dead, starting more paid work buys results with nowhere to land.
  Post-fatal failures don't count as per-item errors and don't retry.

## Spend ceiling

Cost-tracked phases (distill) enforce `sync_max_phase_cost_usd`
(default $25/run, `JOBBUDDY_SYNC_MAX_PHASE_COST_USD`): once the run's
tracked spend crosses the ceiling, workers stop starting paid items and
the producer raises `PhaseCostLimitExceeded` → non-zero exit. The
ceiling is a hard budget on unattended spend, not advice — raise it
deliberately for a known-big backfill, don't remove it. Text bound for
Postgres is NUL-stripped in the store layer (`_strip_nul`), so a
`\x00` in LLM output degrades to a cleaned string instead of a fatal
write.
