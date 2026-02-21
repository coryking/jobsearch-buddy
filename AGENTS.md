# jobsearch-buddy: Agent Guidelines

This is the canonical project context for AI agents working in this repository.
CLAUDE.md points here. Read this before working on code.

## What This Project Is

A command-line tool and MCP server for job searching: scrapes ATS job boards
(Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity, Workable, Eightfold),
caches listings in SQLite, and exposes them via a FastMCP server for use with
Claude Desktop or any MCP-compatible client. Semantic search uses Azure OpenAI
embeddings with sqlite-vec for KNN vector search.

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Package Structure

```
src/jobbuddy/
├── cli/                # Typer CLI (ats command), split into submodules
│   ├── __init__.py     # Main Typer app, shared console
│   ├── sync.py         # ats sync, ats strip, ats embed commands
│   ├── search.py       # ats search, ats list-jobs, ats serve commands
│   ├── jobs.py         # ats save, ats lookup, ats companies commands
│   └── log.py          # ats log command
├── mcp_server.py       # FastMCP server (ats-mcp command)
├── core.py             # Shared logic: fetch, save, URL parsing (no sync)
├── store.py            # JobStore class — SQLite + sqlite-vec (WAL mode, surrogate keys)
├── search.py           # VectorSearch class — sqlite-vec KNN search
├── embeddings.py       # Azure OpenAI text-embedding-3-small (1536 dims)
├── settings.py         # pydantic-settings config (env vars, platformdirs paths)
├── registry.py         # Company registry + fuzzy name matching
├── models.py           # Pydantic Job, FetchResult, Company models
├── url.py              # ATS URL parser
├── job_log.py          # CSV activity log (WA unemployment audit compliance)
├── web.py              # Flask web UI for semantic search
├── companies.json      # Company registry data
├── templates/
│   └── search.html     # Jinja2 template for web search UI
├── sync/               # Sync pipeline (fetch → enrich → strip → embed)
│   ├── __init__.py     # sync_jobs() orchestrator, SyncResult
│   ├── base.py         # WorkerPhase ABC — DB-polling, thread-pooled phase runner
│   ├── display.py      # PhaseState, SyncDisplayState, Rich Live TUI renderer
│   ├── fetch.py        # FetchPhase — parallel company fetching
│   ├── enrich.py       # EnrichPhase — description enrichment for stub fetchers
│   ├── strip.py        # StripPhase — LLM-based boilerplate removal (Azure OpenAI)
│   └── embed.py        # EmbedPhase — Azure OpenAI batch embedding generation
└── fetchers/           # Per-ATS-platform scrapers (strategy pattern)
    ├── base.py         # ATSFetcher ABC
    ├── greenhouse.py
    ├── ashby.py
    ├── lever.py
    ├── workday.py
    ├── eightfold.py
    ├── rippling.py
    ├── paylocity.py
    └── workable.py

tests/
├── test_store.py       # JobStore: schema, upsert, embeddings, migrations
├── test_search.py      # VectorSearch: ranking, limits, sqlite-vec KNN
├── test_embeddings.py  # Serialize/deserialize, embed functions
├── test_sync.py        # Sync orchestration: phases, error isolation
└── test_settings.py    # Settings: defaults, env var overrides, singleton

docs/
├── architecture.md     # Detailed architecture, store design, fetcher pattern
└── data-format.md      # Job search CSV log format
```

## Build / Test Commands

```bash
uv sync                          # Install dependencies
uv run python -m pytest tests/ -v  # Run tests
ats --help                       # CLI help
ats-mcp                          # Run MCP server
```

## Development Workflow: TDD

Use test-driven development for all bug fixes and non-trivial changes:

1. **Write a failing test first** that demonstrates the bug or specifies the behavior
2. **Run the test, confirm it fails** for the right reason
3. **Write the minimum code** to make the test pass
4. **Run the full suite** to confirm no regressions

This applies to bug fixes (write a test that reproduces the bug before fixing it)
and new store/sync features (specify the interface in tests before implementing).
Skip TDD only for trivial changes (typos, config, display-only code).

## CLI Commands

```
ats sync [--company NAME] [--stale HOURS]   # Sync ATS boards into cache
ats strip [--force]                         # Strip boilerplate from descriptions
ats embed                                   # Generate embeddings for stripped descriptions
ats list-jobs [company] [-f FILTER]         # List cached jobs
ats search [--title T] [--location L] [--company C]  # Search cache
ats companies                               # List registered companies
ats save <company> <job_ids...> [-o DIR]    # Save listings as markdown
ats lookup <url>                            # Fetch single job details
ats log <url> [-a ACTION] [-p PERSON] [-n NOTES] [-d DATE]  # Log application
```

## Supported ATS Platforms

| Platform   | URL Pattern                                       |
|------------|---------------------------------------------------|
| Greenhouse | `boards.greenhouse.io/{board}/jobs/{id}`          |
| Ashby      | `jobs.ashbyhq.com/{board}/{uuid}`                 |
| Lever      | `jobs.lever.co/{company}/{uuid}`                  |
| Workday    | `{company}.wd{N}.myworkdayjobs.com/...`           |
| Rippling   | `ats.rippling.com/{company}/jobs/{uuid}`          |
| Workable   | `apply.workable.com/{board}/j/{shortcode}`        |
| Paylocity  | `recruiting.paylocity.com/Recruiting/Jobs/...`    |
| Eightfold  | various (e.g. Microsoft)                          |

## Configuration

Settings are managed by `src/jobbuddy/settings.py` (pydantic-settings).
Override defaults with env vars (prefix `JOBBUDDY_`) or a `.env` file:

| Setting                  | Env Var                              | Default                                   |
|--------------------------|--------------------------------------|-------------------------------------------|
| `data_dir`               | `JOBBUDDY_DATA_DIR`                  | `~/projects/resume/data`                  |
| `db_path`                | `JOBBUDDY_DB_PATH`                   | platformdirs `user_data_dir/jobs_cache.db`|
| `listings_dir`           | `JOBBUDDY_LISTINGS_DIR`              | `~/projects/resume/job-listings`          |
| `openai_api_key`         | `JOBBUDDY_OPENAI_API_KEY`            | `None` *(enables strip/embed/search)*     |
| `openai_base_url`        | `JOBBUDDY_OPENAI_BASE_URL`           | `None` *(omit for api.openai.com)*        |
| `openai_azure_api_version` | `JOBBUDDY_OPENAI_AZURE_API_VERSION`| `None` *(if set, uses AzureOpenAI client)*|
| `strip_model`            | `JOBBUDDY_STRIP_MODEL`               | `gpt-5-nano`                              |
| `embedding_model`        | `JOBBUDDY_EMBEDDING_MODEL`           | `text-embedding-3-small`                  |

## Sync Pipeline

The sync pipeline uses a **DB-as-queue** pattern with four phases:

1. **Fetch** — parallel company fetching via ThreadPoolExecutor
2. **Enrich** — description enrichment for stub fetchers (Workday, Eightfold, etc.)
3. **Strip** — LLM-based boilerplate removal via OpenAI-compatible API
4. **Embed** — batch embedding generation via OpenAI-compatible API

Strip and embed are optional — they only run when `JOBBUDDY_OPENAI_API_KEY` is
set. Without it, `ats sync` runs fetch + enrich only.

Each phase (strip, enrich, embed) extends the `WorkerPhase` ABC (`sync/base.py`),
which provides: DB polling for work items, `ThreadPoolExecutor` parallelism,
per-thread DB connections, graceful shutdown via `threading.Event`, and display
state updates. Phases poll the database for unprocessed items, process them in
worker threads, and write results back. This decouples phases — each can run
independently via standalone CLI commands (`ats strip`, `ats embed`).

**Rate limiting:** Embedding pacing uses `x-ratelimit-remaining-tokens` response
headers (tested on Azure OpenAI). If your provider returns these headers, pacing
activates automatically. Without them, no pacing occurs — you're responsible for
staying within your provider's limits.

Display uses Rich Live with `PhaseState` objects (`sync/display.py`). Phase
workers update `PhaseState` attributes directly (GIL-atomic writes); the Rich
Live renderer polls at 4hz. `RollingRate` tracks items/min from a 60-second
sliding window of timestamps.

## Architecture Conventions

- `core.py` raises `ValueError`; callers (CLI, MCP) handle presentation
- CLI and MCP both call into `core.py` — shared logic, dual interface
- All search reads from SQLite cache; only `ats sync` touches the network
- Tests use `:memory:` SQLite or `tmp_path` — fast, no real DB or network

See `docs/architecture.md` for detailed architecture documentation.
See `src/jobbuddy/CLAUDE.md` for package-specific coding guidance.
