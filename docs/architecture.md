# Architecture

## Overview

jobsearch-buddy has two interfaces sharing a single core:

- **CLI** (`ats` command, Typer) — interactive use, rich output
- **MCP server** (`ats-mcp`, FastMCP) — Claude Desktop integration

Both call into `core.py`. Core raises `ValueError`; callers handle presentation.

## Cache Design

All search/browse reads come from a local SQLite cache. Only `ats sync` touches
the network for bulk listing fetches.

| Operation | Source | Notes |
|-----------|--------|-------|
| `search_jobs` MCP | Cache | Cross-company keyword search |
| `semantic_search_jobs` MCP | Cache | Vector similarity (fastembed) |
| `ats list-jobs` CLI | Cache | Optional company filter |
| `ats search` CLI | Cache | Title/location/company filters |
| `ats sync` CLI | Live API | Populates/refreshes cache |
| `get_job_post_details` MCP | Live API | Needs full descriptions |
| `log_job_application` MCP | Live API | Saves listing as markdown |
| `ats lookup` CLI | Live API | Single-job detail fetch |

### Schema

```sql
jobs          -- company_slug + job_id PK, title, location, url, published_at,
              --   salary, team, department, description, disappeared_at
sync_status   -- per-company last sync time and error state
vec_jobs      -- sqlite-vec virtual table for KNN vector search
job_embeddings -- maps vec0 integer rowids to (company_slug, job_id) PK
```

### Soft-Delete

When a job disappears from a company's feed, it gets `disappeared_at` instead of
deletion. Jobs that reappear get `disappeared_at = NULL`. `query_jobs()` excludes
disappeared jobs by default; pass `include_disappeared=True` to see them.

### Vector Search

Jobs with descriptions are embedded during sync via `generate_embeddings()`.
Embeddings stored in `vec_jobs` (sqlite-vec virtual table). `job_embeddings`
bridges vec0's integer rowids to composite `(company_slug, job_id)` PKs.
`semantic_search()` does KNN via `WHERE v.embedding MATCH ?`.

Model: Snowflake arctic-embed-l (1024 dims, ~1GB download on first use).
Asymmetric retrieval: `embed_texts()` for documents (passage prefix),
`embed_query()` for search queries.

### Sync Concurrency

`ats sync` uses `ThreadPoolExecutor(5)` — workers fetch in parallel, main thread
writes sequentially. Companies are shuffled before sync to spread same-platform
requests (rate-limit mitigation). Each company is error-isolated.

## Fetcher Architecture

Strategy pattern. Each ATS platform is a class inheriting `ATSFetcher`
(in `fetchers/base.py`). Board slug is instance state.

Factory functions in `fetchers/__init__.py`:
- `get_fetcher(slug)` — looks up company in registry, returns configured instance
- `create_fetcher(ats_type, board=..., **kw)` — low-level, explicit params

`list_jobs()` fetches discovery metadata (may include descriptions).
`fetch_job()` fetches a single job's full details.

**Stub vs Full fetchers:** Workday, Eightfold, Oracle HCM don't return
descriptions in bulk listings. After sync, `core.py` runs description enrichment:
calls `fetch_job()` for stub-fetcher jobs that lack descriptions.

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
