# Architecture

## Overview

jobsearch-buddy has two interfaces sharing a single core:

- **CLI** (`jsb` command, Typer) — interactive use, rich output
- **MCP server** (`jsb-mcp`, FastMCP) — Claude Desktop integration

Both call into `core.py`. Core raises `ValueError`; callers handle presentation.

## Data Access — JobStore

`store.py` provides the `JobStore` class — a PostgreSQL data access layer with
surrogate keys and pgvector for vector search. All DB access goes through
JobStore; no raw `conn` passing. Each `JobStore` instance owns a single `psycopg`
connection; worker threads in the sync pipeline each create their own instance
via `_get_thread_store()`.

| Operation | Source | Notes |
|-----------|--------|-------|
| `search_jobs` MCP | JobStore | Cross-company keyword search |
| `semantic_search_jobs` MCP | VectorSearch | sqlite-vec KNN cosine search |
| `jsb list-jobs` CLI | JobStore | Optional company filter |
| `jsb search` CLI | JobStore | Title/location/company filters |
| `jsb sync` CLI | Live API | Populates/refreshes cache |
| `get_job_post_details` MCP | Live API | Needs full descriptions |
| `log_job_application` MCP | Live API | Saves listing as markdown |
| `jsb lookup` CLI | Live API | Single-job detail fetch |

### Schema

```sql
jobs             -- SERIAL PK (surrogate), UNIQUE(company_slug, job_id)
                 --   title, location, url, published_at, salary, team,
                 --   department, description, description_stripped,
                 --   ats_metadata (JSONB), embedding vector(1536),
                 --   last_seen, listing_status (enum: active/removed),
                 --   removed_at
sync_status      -- per-company last sync time and error state

-- HNSW index for vector similarity search
idx_jobs_embedding  -- USING hnsw (embedding vector_cosine_ops)
```

Single embedding model (text-embedding-3-small, 1536 dims).
Embeddings stored inline on the jobs table as `vector(1536)`.
HNSW index enables filtered vector search without oversampling.

### Soft-Delete

When a job disappears from a company's feed, its `listing_status` is set to
`'removed'`. A database trigger auto-manages `removed_at` — setting it on
removal and clearing it on reactivation. Jobs that reappear get
`listing_status = 'active'` and `removed_at = NULL`. `query_jobs()` excludes
removed jobs by default. Re-postings (removed → active) are logged at INFO.

## Vector Search

`search.py` provides the `VectorSearch` class — owns a `JobStore` and delegates
to pgvector for HNSW search. Search consumers (web.py, mcp_server.py) create one.

Search flow:
1. `embed_query(query)` → query vector via OpenAI-compatible text-embedding-3-small
2. `store.search_similar(vector, k)` → HNSW search via pgvector `<=>` operator
3. Return ranked `SearchResult` list (score = 1.0 - cosine distance)

HNSW indexes support native filtered search (e.g. by company or active status)
without the oversampling workaround that sqlite-vec required.

### Embedding Model

`embeddings.py` is a thin OpenAI-compatible wrapper:

| Key | Deployment | Dimensions |
|-----|-----------|------------|
| `text3small` | text-embedding-3-small | 1536 |

Single model, no registry. `embed_texts()` handles batches (up to 2048 per API
call), `embed_query()` handles single search queries. pgvector handles
serialization natively via psycopg.

## Sync Pipeline

`sync/` package orchestrates four phases:

1. **FetchPhase** (`sync/fetch.py`): Parallel company fetching via ThreadPoolExecutor
2. **EnrichPhase** (`sync/enrich.py`): Description enrichment for stub fetchers
3. **StripPhase** (`sync/strip.py`): LLM-based boilerplate removal (OpenAI-compatible API, default gpt-5-nano)
4. **EmbedPhase** (`sync/embed.py`): Batch embedding generation (OpenAI-compatible API, default text-embedding-3-small)

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

Standalone commands (`jsb strip`, `jsb embed`) pass `filter_phases` to show
only their relevant phase row.

### Sync Concurrency

Fetch uses `ThreadPoolExecutor(max_workers)`. Companies are shuffled before sync
to spread same-platform requests (rate-limit mitigation). Each company is
error-isolated. Strip runs ~60 workers (RPM-bound). Embed runs
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
| `data_dir` | platformdirs `user_data_dir/data` |
| `pg_service` | `job-search-buddy-remote` |
| `listings_dir` | platformdirs `user_data_dir/listings` |
| `openai_api_key` | `None` *(enables strip/embed/search)* |
| `openai_base_url` | `None` *(omit for api.openai.com)* |
| `openai_azure_api_version` | `None` *(if set, uses AzureOpenAI client)* |
| `strip_model` | `gpt-5-nano` |
| `embedding_model` | `text-embedding-3-small` |

Env var prefix: `JOBBUDDY_`. OpenAI API credentials are required for strip,
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
