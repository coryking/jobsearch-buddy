# Architecture

## Overview

jobsearch-buddy has two interfaces sharing a single core:

- **CLI** (`ats` command, Typer) — interactive use, rich output
- **MCP server** (`ats-mcp`, FastMCP) — Claude Desktop integration

Both call into `core.py`. Core raises `ValueError`; callers handle presentation.

## Data Access — JobStore

`store.py` provides the `JobStore` class — a SQLite data access layer with WAL
mode, surrogate keys, and optional thread-safe writes. All DB access goes through
JobStore; no raw `conn` passing.

| Operation | Source | Notes |
|-----------|--------|-------|
| `search_jobs` MCP | JobStore | Cross-company keyword search |
| `semantic_search_jobs` MCP | VectorSearch | NumPy cosine similarity |
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
                 --   department, description, ats_metadata, last_seen,
                 --   disappeared_at
sync_status      -- per-company last sync time and error state
embedding_models -- model_key PK, model_name, dimensions, created_at
job_embeddings   -- FK to jobs.id, FK to embedding_models.model_key
                 --   embedding BLOB (raw float32), text_hash
```

### Soft-Delete

When a job disappears from a company's feed, it gets `disappeared_at` instead of
deletion. Jobs that reappear get `disappeared_at = NULL`. `query_jobs()` excludes
disappeared jobs by default.

## Vector Search

`search.py` provides the `VectorSearch` class — owns a `JobStore` and the
embedding model registry. Search consumers (web.py, mcp_server.py) create one.

Search flow:
1. `embed_query(query, model_key)` → query vector (with query prefix if needed)
2. `store.load_embeddings(model_key)` → NumPy matrix + job ID list from BLOBs
3. Cosine similarity via `matrix @ query_vec` (vectors normalized by sentence-transformers)
4. Top K job IDs → `SELECT * FROM jobs WHERE id IN (...)`
5. Return ranked `SearchResult` list

No in-memory cache — SQLite loads ~5K BLOBs per request fast enough (~50-100ms).
Sync and search run in separate processes, so no cache coherence issues.

### Embedding Models

`embeddings.py` defines a model registry with three models:

| Key | Model | Dimensions | Notes |
|-----|-------|------------|-------|
| `bge_small` | BAAI/bge-small-en-v1.5 | 384 | Default. Fast, known quantity |
| `nomic_v15` | nomic-ai/nomic-embed-text-v1.5 | 768 | Asymmetric (query/passage prefixes) |
| `jobbge_m3` | pj-mathematician/JobBGE-m3 | 1024 | Job-domain fine-tuned |

Models are lazy-loaded via `get_model()` and cached in a dict. Adding a model =
one `EmbeddingModelConfig` entry in `MODEL_REGISTRY`.

## Sync Pipeline

`sync/` package orchestrates three phases:

1. **FetchPhase** (`sync/fetch.py`): Parallel company fetching via ThreadPoolExecutor
2. **EnrichPhase** (`sync/enrich.py`): Description enrichment for stub fetchers
3. **EmbedPhase** (`sync/embed.py`): Per-model sequential embedding generation

`sync_jobs()` in `sync/__init__.py` coordinates them. `SyncCallbacks` dataclass
replaces positional callback parameters for progress reporting.

### Sync Concurrency

Fetch and enrich phases use `ThreadPoolExecutor(max_workers)`. Companies are
shuffled before sync to spread same-platform requests (rate-limit mitigation).
Each company is error-isolated. The embed phase runs models sequentially (they'd
fight over CPU/GPU if parallel).

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

Env var prefix: `JOBBUDDY_`.

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
