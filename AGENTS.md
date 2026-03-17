# jobsearch-buddy: Agent Guidelines

This is the canonical project context for AI agents working in this repository.
CLAUDE.md points here. Read this before working on code.

## What This Project Is

A command-line tool and MCP server for job searching: scrapes ATS job boards
(Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity, Workable, Eightfold,
Oracle HCM), caches listings in PostgreSQL, and exposes them via a FastMCP server
for use with Claude Desktop or any MCP-compatible client. Semantic search uses
OpenAI-compatible embeddings with pgvector HNSW indexes.

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Package Structure

```
src/jobbuddy/
├── cli/                # Typer CLI (jsb command), split into submodules
│   ├── __init__.py     # Main Typer app, shared console
│   ├── sync.py         # jsb sync command (phase-selective)
│   ├── search.py       # jsb search, jsb list-jobs, jsb serve commands
│   ├── jobs.py         # jsb save, jsb lookup, jsb companies commands
│   └── log.py          # jsb log command
├── mcp_server.py       # FastMCP server (jsb-mcp command)
├── core.py             # Shared logic: fetch, save, URL parsing (no sync)
├── store.py            # JobStore class — PostgreSQL + pgvector (surrogate keys)
├── search.py           # VectorSearch class — pgvector HNSW search
├── embeddings.py       # OpenAI-compatible text-embedding-3-small (1536 dims)
├── settings.py         # pydantic-settings config (env vars, platformdirs paths)
├── registry.py         # Company registry + fuzzy name matching
├── models.py           # Pydantic Job, FetchResult, Company models
├── url.py              # ATS URL parser
├── job_log.py          # CSV activity log (WA unemployment audit compliance)
├── web.py              # Flask web UI for semantic search
├── templates/
│   └── search.html     # Jinja2 template for web search UI
├── sync/               # Sync pipeline (fetch → enrich → strip → embed)
│   ├── __init__.py     # sync_jobs() orchestrator, SyncResult
│   ├── base.py         # WorkerPhase ABC — DB-polling, thread-pooled phase runner
│   ├── display.py      # PhaseState, SyncDisplayState, Rich Live TUI renderer
│   ├── fetch.py        # FetchPhase — parallel company fetching
│   ├── enrich.py       # EnrichPhase — description enrichment for stub fetchers
│   ├── strip.py        # StripPhase — LLM-based boilerplate removal (OpenAI-compatible)
│   └── embed.py        # EmbedPhase — OpenAI-compatible batch embedding generation
└── fetchers/           # Per-ATS-platform scrapers (strategy pattern)
    ├── base.py         # ATSFetcher ABC
    ├── greenhouse.py
    ├── ashby.py
    ├── lever.py
    ├── workday.py
    ├── eightfold.py
    ├── eightfold_v2.py
    ├── oracle_hcm.py
    ├── rippling.py
    ├── paylocity.py
    └── workable.py

tests/
├── test_store.py       # JobStore: schema, upsert, embeddings, migrations
├── test_search.py      # VectorSearch: ranking, limits, pgvector HNSW
├── test_embeddings.py  # Serialize/deserialize, embed functions
├── test_sync.py        # Sync orchestration: phases, error isolation
└── test_settings.py    # Settings: defaults, env var overrides, singleton

docs/
├── architecture.md     # Detailed architecture, store design, fetcher pattern
└── throughput-reference.md  # Sync pipeline throughput benchmarks
```

## Build / Test Commands

```bash
uv sync                          # Install dependencies
uv run python -m pytest tests/ -v  # Run tests
jsb migrate                      # Apply pending database migrations
jsb --help                       # CLI help
jsb-mcp                          # Run MCP server
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
jsb migrate                                  # Apply pending database migrations
jsb sync [PHASES...] [--company NAME] [--stale HOURS] [--force]  # Sync pipeline (phases: fetch, enrich, strip, embed)
jsb list-jobs [company] [-f FILTER]         # List cached jobs
jsb search [--title T] [--location L] [--company C]  # Search cache
jsb companies                               # List registered companies
jsb save <company> <job_ids...> [-o DIR]    # Save listings as markdown
jsb lookup <url>                            # Fetch single job details
jsb log <url> [-a ACTION] [-p PERSON] [-n NOTES] [-d DATE]  # Log application
```

## Supported ATS Platforms

| Platform    | URL Pattern                                                  |
|-------------|--------------------------------------------------------------|
| Greenhouse  | `boards.greenhouse.io/{board}/jobs/{id}`                     |
| Ashby       | `jobs.ashbyhq.com/{board}/{uuid}`                            |
| Lever       | `jobs.lever.co/{company}/{uuid}`                             |
| Workday     | `{company}.wd{N}.myworkdayjobs.com/...`                     |
| Rippling    | `ats.rippling.com/{company}/jobs/{uuid}`                     |
| Workable    | `apply.workable.com/{board}/j/{shortcode}`                   |
| Paylocity   | `recruiting.paylocity.com/Recruiting/Jobs/...`               |
| Eightfold   | various (e.g. Microsoft)                                     |
| Oracle HCM  | `{tenant}.fa.{region}.oraclecloud.com/.../sites/{site}/job/{id}` |

## Configuration

Settings are managed by `src/jobbuddy/settings.py` (pydantic-settings).
Override defaults with env vars (prefix `JOBBUDDY_`) or a `.env` file:

| Setting                    | Env Var                              | Default                                    |
|----------------------------|--------------------------------------|--------------------------------------------|
| `data_dir`                 | `JOBBUDDY_DATA_DIR`                  | platformdirs `user_data_dir/data`          |
| `pg_service`               | `JOBBUDDY_PG_SERVICE`                | `job-search-buddy-remote`                  |
| `listings_dir`             | `JOBBUDDY_LISTINGS_DIR`              | platformdirs `user_data_dir/listings`      |
| `openai_api_key`           | `JOBBUDDY_OPENAI_API_KEY`            | `None` *(required for strip/embed/search)* |
| `openai_base_url`          | `JOBBUDDY_OPENAI_BASE_URL`           | `None` *(omit for api.openai.com)*         |
| `openai_azure_api_version` | `JOBBUDDY_OPENAI_AZURE_API_VERSION`  | `None` *(if set, uses AzureOpenAI client)* |
| `strip_model`              | `JOBBUDDY_STRIP_MODEL`               | `gpt-5-nano`                               |
| `embedding_model`          | `JOBBUDDY_EMBEDDING_MODEL`           | `text-embedding-3-small`                   |
| `strip_batch_size`         | `JOBBUDDY_STRIP_BATCH_SIZE`          | `50`                                       |

## Sync Pipeline

The sync pipeline uses a **DB-as-queue** pattern with four phases:

1. **Fetch** — parallel company fetching via ThreadPoolExecutor
2. **Enrich** — description enrichment for stub fetchers (Workday, Eightfold, etc.)
3. **Strip** — LLM-based boilerplate removal via OpenAI-compatible API
4. **Embed** — batch embedding generation via OpenAI-compatible API

`jsb sync` runs all four phases by default. Strip and embed require
`JOBBUDDY_OPENAI_API_KEY` — sync fails fast at startup if it's missing.
Use `jsb sync fetch enrich` to run without OpenAI credentials.

Preconditions (phase names, OpenAI key, company resolution) are validated
up front by `validate_sync_config()` before any I/O. The orchestrator
(`sync_jobs()`) trusts the caller and does not re-validate.

All phases update `PhaseState` objects directly for display. The fetch phase
uses the same pattern as enrich/strip/embed — no event queue.

Each phase (enrich, strip, embed) extends the `WorkerPhase` ABC (`sync/base.py`),
which provides: DB polling for work items, `ThreadPoolExecutor` parallelism,
per-thread DB connections, graceful shutdown via `threading.Event`, and display
state updates. Phases poll the database for unprocessed items, process them in
worker threads, and write results back. This decouples phases — each can run
independently via phase selection (`jsb sync strip`, `jsb sync embed`).

**Rate limiting:** Embedding pacing uses `x-ratelimit-remaining-tokens` response
headers. If your provider returns these headers, pacing activates automatically.
Without them, no pacing occurs — you're responsible for staying within your
provider's limits.

Display uses Rich Live with `PhaseState` objects (`sync/display.py`). Phase
workers update `PhaseState` attributes directly (GIL-atomic writes); the Rich
Live renderer polls at 4hz. `RollingRate` tracks items/min from a 60-second
sliding window of timestamps.

## Schema Migrations

Migrations are **explicit and manual** — run `jsb migrate` to apply them.
`JobStore` does not auto-migrate on connection. This prevents accidental schema
changes from scripts, MCP servers, or other code that instantiates a `JobStore`.

Migration files live in `src/jobbuddy/migrations/` as numbered SQL files
(e.g. `001_initial.sql`). The `schema_migrations` table tracks which have been
applied. After adding a new migration file, run `jsb migrate` to apply it.

Tests apply migrations once per session via the `ensure_pg_schema` fixture in
`tests/conftest.py`.

## Architecture Conventions

- `core.py` raises `ValueError`; callers (CLI, MCP) handle presentation
- CLI and MCP both call into `core.py` — shared logic, dual interface
- All search reads from PostgreSQL cache; only `jsb sync` touches the network
- Migrations are explicit — `JobStore` never auto-migrates (see above)
- Tests use a dedicated test PostgreSQL database — fast, no network

See `docs/architecture.md` for detailed architecture documentation.
See `src/jobbuddy/CLAUDE.md` for package-specific coding guidance.
