# Architecture

## Overview

jobsearch-buddy has two interfaces sharing a single core:

- **CLI** (`ats` command, Typer) — interactive use, rich output
- **MCP server** (`ats-mcp`, FastMCP) — Claude Desktop integration

Both call into `core.py`. Core raises `ValueError`; callers handle presentation.

## Data Access — JobStore

`store.py` provides the `JobStore` class — a SQLite data access layer with WAL
mode, surrogate keys, and sqlite-vec for vector search. All DB access goes through
JobStore; no raw `conn` passing. Each `JobStore` instance owns a single `sqlite3`
connection; worker threads in the sync pipeline each create their own instance
via `_get_thread_store()`.

| Operation | Source | Notes |
|-----------|--------|-------|
| `search_jobs` MCP | JobStore | Cross-company keyword search |
| `semantic_search_jobs` MCP | VectorSearch | sqlite-vec KNN cosine search |
| `ats list-jobs` CLI | JobStore | Optional company filter |
| `ats search` CLI | JobStore | Title/location/company filters |
| `ats sync` CLI | Live API | Populates/refreshes cache |
| `get_job_post_details` MCP | Live API | Needs full descriptions |
| `log_job_application` MCP | Live API | Saves listing as markdown |
| `ats lookup` CLI | Live API | Single-job detail fetch |

### Schema

```sql
jobs             -- INTEGER PK (surrogate), UNIQUE(company_slug, job_id)
                 --   title, location, url, published_at, salary, team,
                 --   department, description, description_stripped,
                 --   ats_metadata, last_seen, disappeared_at
sync_status      -- per-company last sync time and error state
job_embeddings   -- job_id PK (FK to jobs.id), text_hash, embedding BLOB

-- sqlite-vec virtual table for KNN search
vec_jobs         -- vec0 virtual table: job_id INTEGER PK,
                 --   embedding float[1536] distance_metric=cosine
```

Single embedding model (Azure OpenAI text-embedding-3-small, 1536 dims).
Embeddings are dual-written: `job_embeddings` stores the BLOB with `text_hash`
for change tracking, `vec_jobs` indexes the same vector for KNN queries.

### Soft-Delete

When a job disappears from a company's feed, it gets `disappeared_at` instead of
deletion. Jobs that reappear get `disappeared_at = NULL`. `query_jobs()` excludes
disappeared jobs by default.

## Vector Search

`search.py` provides the `VectorSearch` class — owns a `JobStore` and delegates
to sqlite-vec for KNN search. Search consumers (web.py, mcp_server.py) create one.

Search flow:
1. `embed_query(query)` → query vector via Azure OpenAI text-embedding-3-small
2. `serialize_f32(vector)` → little-endian bytes for sqlite-vec
3. `store.search_similar(query_blob, k)` → KNN via `vec_jobs` virtual table
4. Return ranked `SearchResult` list (score = 1.0 - cosine distance)

Query latency is ~10-15ms for the sqlite-vec KNN step plus ~200ms for the Azure
embedding API call.

### Embedding Model

`embeddings.py` is a thin Azure OpenAI wrapper:

| Key | Deployment | Dimensions |
|-----|-----------|------------|
| `text3small` | text-embedding-3-small | 1536 |

Single model, no registry. `embed_texts()` handles batches (up to 2048 per API
call), `embed_query()` handles single search queries. `serialize_f32()` /
`deserialize_f32()` convert between float lists and BLOB bytes.

## Sync Pipeline

`sync/` package orchestrates four phases:

1. **FetchPhase** (`sync/fetch.py`): Parallel company fetching via ThreadPoolExecutor
2. **EnrichPhase** (`sync/enrich.py`): Description enrichment for stub fetchers
3. **StripPhase** (`sync/strip.py`): LLM-based boilerplate removal (Azure OpenAI gpt-5-nano)
4. **EmbedPhase** (`sync/embed.py`): Batch embedding generation (Azure OpenAI text-embedding-3-small)

### DB-as-Queue Pattern

Phases 2-4 extend the `WorkerPhase` ABC (`sync/base.py`). Each phase polls the
database for unprocessed work items, processes them in a `ThreadPoolExecutor`,
and writes results back. This decouples phases from each other — the database
is the coordination mechanism, not in-memory queues or callbacks.

`WorkerPhase` provides:
- `count_remaining()` / `poll_work(batch_size)` / `process_item(item)` — abstract methods subclasses implement
- `ThreadPoolExecutor` with configurable `max_workers`
- Per-thread DB connections via `threading.local()` and `_get_thread_store()`
- Graceful shutdown via `threading.Event`
- `PhaseState` display updates on advance/error

### Display

`sync/display.py` provides a Rich Live TUI. `PhaseState` dataclass tracks each
phase's progress (done/total, errors, rolling rate, last detail text). Phase
workers update `PhaseState` attributes directly — simple attribute writes are
GIL-atomic. `RollingRate` tracks items/min from a 60-second sliding window of
completion timestamps, showing rate dropping to zero when nothing completes
(unlike cumulative averages that hide stalls). The Rich Live renderer polls at
4hz via `create_live()`.

Standalone commands (`ats strip`, `ats embed`) pass `filter_phases` to show
only their relevant phase row.

### Sync Concurrency

Fetch uses `ThreadPoolExecutor(max_workers)`. Companies are shuffled before sync
to spread same-platform requests (rate-limit mitigation). Each company is
error-isolated. Strip runs ~60 workers (Azure OpenAI RPM-bound). Embed runs
~4 workers with batches of ~173 items (targeting ~25% of 1M TPM per batch).
Enrich runs ~5 workers, sequential per-company for rate-limit mitigation.

## Fetcher Architecture

Strategy pattern. Each ATS platform is a class inheriting `ATSFetcher`
(in `fetchers/base.py`). Board slug is instance state.

Factory functions in `fetchers/__init__.py`:
- `get_fetcher(slug)` — looks up company in registry, returns configured instance
- `create_fetcher(ats_type, board=..., **kw)` — low-level, explicit params

`list_jobs()` fetches discovery metadata (may include descriptions).
`fetch_job()` fetches a single job's full details.

**Stub vs Full fetchers:** Workday, Eightfold, Oracle HCM don't return
descriptions in bulk listings. After sync, the enrich phase calls
`fetch_descriptions()` for stub-fetcher jobs that lack descriptions.

## Settings

`settings.py` uses pydantic-settings. Priority: env vars > defaults.

| Field | Default |
|-------|---------|
| `data_dir` | `~/projects/resume/data` |
| `db_path` | platformdirs `user_data_dir/jobsearch-buddy/jobs_cache.db` |
| `listings_dir` | `~/projects/resume/job-listings` |
| `azure_openai_endpoint` | *(required)* |
| `azure_openai_api_key` | *(required)* |
| `azure_openai_api_version` | `2024-10-21` |

Env var prefix: `JOBBUDDY_`. Azure OpenAI credentials are required for strip,
embed, and semantic search operations.

## Company Registry

`companies.json` is the canonical company directory. Entries with `ats: null`
are valid for activity tracking but don't support scraping.

Fuzzy lookup: `lookup_by_name()` tries exact match on name/slug first, then
normalized alphanumeric match (strips spaces, case, special chars).

## Saved Listings Format

`{listings_dir}/{slug}/{YYYY-MM-DD}_{slugified-title}_{job_id}.md`

Markdown structure: `# Title`, `## Essentials` (metadata bullets),
`## Description` (plaintext, HTML stripped).

## MCP Tool Descriptions

Tool descriptions and field descriptions in `mcp_server.py` are injected into
the LLM's context. Write them as routing hints, not API docs:

- **Server `instructions`**: Intent language that matches natural queries.
  Name specific companies. Bias toward trying the tool first.
- **Tool docstrings**: Lead with *when to use*, not *what it does*.
- **Field `description`s**: Format hints, examples, valid values. Dense, not verbose.
