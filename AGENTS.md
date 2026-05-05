# jobsearch-buddy: Agent Guidelines

This is the canonical project context for AI agents working in this repository.
CLAUDE.md points here. Read this before working on code.

**The user is the human who installed the MCP server, not the LLM calling it.**
When analyzing usage, designing parameters, writing descriptions, or prioritizing
work — start from what the human was trying to do and whether they got a good
outcome. It's easy to look at how the LLM called us and ask "how can we make
this easier?" with an implicit "for the LLM." That optimizes the intermediary,
not the outcome. The LLM is infrastructure; the human searching for jobs is
the user.

## What This Project Is

A command-line tool and MCP server for job searching: scrapes ATS job boards
(see Supported ATS Platforms below), caches listings in PostgreSQL, and exposes
them via a FastMCP server for use with Claude Desktop or any MCP-compatible
client. Search uses PostgreSQL full-text search over a per-job distill
pipeline that produces `short_jd`, `description_normalized`, and `salary`.
The `pgvector` extension stays installed for Phase 2 (company-side work).

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Package Structure

```
src/jobbuddy/
├── cli/                # Typer CLI (jsb command), split into submodules
│   ├── __init__.py     # Main Typer app, shared console
│   ├── sync.py         # jsb sync command (phase-selective)
│   ├── search.py       # jsb search, jsb list-jobs commands
│   ├── jobs.py         # jsb save, jsb lookup, jsb companies commands
│   └── log.py          # jsb log command
├── mcp_server.py       # FastMCP server (jsb-mcp command)
├── core.py             # Shared logic: fetch, save, URL parsing (no sync)
├── store.py            # JobStore class — PostgreSQL persistence (surrogate keys)
├── settings.py         # pydantic-settings config (env vars, platformdirs paths)
├── registry.py         # Company registry + fuzzy name matching
├── models.py           # Pydantic Job, FetchResult, Company models
├── url.py              # ATS URL parser
├── job_log.py          # CSV activity log (WA unemployment audit compliance)
├── sync/               # Sync pipeline (fetch → enrich → distill)
│   ├── __init__.py     # sync_jobs() orchestrator, SyncResult
│   ├── base.py         # WorkerPhase ABC — DB-polling, thread-pooled phase runner
│   ├── display.py      # PhaseState, SyncDisplayState, Rich Live TUI renderer
│   ├── fetch.py        # FetchPhase — parallel company fetching
│   ├── enrich.py       # EnrichPhase — description enrichment for stub fetchers
│   └── distill.py      # DistillPhase — three-field LLM distill (Unit 2)
└── fetchers/           # Per-ATS scrapers — one file per platform (see Supported ATS Platforms)
    ├── base.py         # ATSFetcher ABC
    ├── __init__.py     # Fetcher registry + factory
    └── {platform}.py   # One module per ATS (greenhouse.py, workday.py, etc.)

tests/
├── test_store.py       # JobStore: schema, upsert, FTS, migrations
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

## CLI Commands

```
jsb migrate                                  # Apply pending database migrations
jsb sync [PHASES...] [--company NAME] [--stale HOURS]  # Sync pipeline (phases: fetch, enrich, distill)
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
| Phenom      | `{careers_domain}/{country}/{lang}/job/{id}`                 |
| SuccessFactors | `{careers_domain}/job/{slug}/{id}/`                       |
| Jibe        | `{careers_domain}/jobs/{id}` (iCIMS Attract layer)           |
| JobSync     | `{careers_domain}/jobs/{slug}/{guid}` (Solr search layer)    |
| SmartRecruiters | `jobs.smartrecruiters.com/{company}/{id}-{slug}`         |
| Tesla       | `tesla.com/careers/search/job/{slug}-{id}`                   |

## Configuration

Settings are managed by `src/jobbuddy/settings.py` (pydantic-settings).
Override defaults with env vars (prefix `JOBBUDDY_`) or a `.env` file:

| Setting                    | Env Var                              | Default                                    |
|----------------------------|--------------------------------------|--------------------------------------------|
| `data_dir`                 | `JOBBUDDY_DATA_DIR`                  | platformdirs `user_data_dir/data`          |
| `pg_service`               | `JOBBUDDY_PG_SERVICE`                | `job-search-buddy-remote`                  |
| `postgres_host`            | `JOBBUDDY_POSTGRES_HOST`             | `None` *(set to enable Azure Entra auth)*  |
| `postgres_database`        | `JOBBUDDY_POSTGRES_DATABASE`         | `None`                                     |
| `postgres_user`            | `JOBBUDDY_POSTGRES_USER`             | `None` *(managed identity name)*           |
| `listings_dir`             | `JOBBUDDY_LISTINGS_DIR`              | platformdirs `user_data_dir/listings`      |
| `openai_api_key`           | `JOBBUDDY_OPENAI_API_KEY`            | `None` *(required for the distill phase)*  |
| `openai_base_url`          | `JOBBUDDY_OPENAI_BASE_URL`           | `None` *(omit for api.openai.com)*         |
| `openai_azure_api_version` | `JOBBUDDY_OPENAI_AZURE_API_VERSION`  | `None` *(if set, uses AzureOpenAI client)* |
| `distill_model`            | `JOBBUDDY_DISTILL_MODEL`             | `gpt-5-nano`                               |
| `distill_prompt_version`   | `JOBBUDDY_DISTILL_PROMPT_VERSION`    | `distill-v1`                               |

## Sync Pipeline

The sync pipeline uses a **DB-as-queue** pattern with three phases:

1. **Fetch** — parallel company fetching via ThreadPoolExecutor
2. **Enrich** — description enrichment for stub fetchers (Workday, Eightfold, etc.)
3. **Distill** — one structured-output LLM call per job; produces
   `short_jd`, `description_normalized`, and `salary` in a single round trip

`jsb sync` runs all three phases by default. Distill requires OpenAI
credentials — either `JOBBUDDY_OPENAI_API_KEY` (local) or
`JOBBUDDY_OPENAI_AZURE_API_VERSION` with managed identity (Azure). Sync
fails fast at startup if neither is configured. Use `jsb sync fetch enrich`
to run without OpenAI credentials.

Preconditions (phase names, OpenAI key, company resolution) are validated
up front by `validate_sync_config()` before any I/O. The orchestrator
(`sync_jobs()`) trusts the caller and does not re-validate.

All phases update `PhaseState` objects directly for display. The fetch phase
uses the same pattern as enrich/distill — no event queue.

`EnrichPhase` and `DistillPhase` extend the `WorkerPhase` ABC (`sync/base.py`),
which provides: DB polling for work items, `ThreadPoolExecutor` parallelism,
DB writes via a single-threaded `WriteQueue`, graceful shutdown via
`threading.Event`, and display state updates. Phases poll the database for
unprocessed items, process them in worker threads, and write results back.
Each phase can also run independently via phase selection
(`jsb sync distill`).

The distill phase's "needs work" predicate is a stable column-presence check
(`short_jd IS NULL AND description IS NOT NULL`) — no hash. The upsert
nulls `short_jd`/`description_normalized` whenever a job's `description`
body changes, so the distill phase picks the row up again on the next pass.

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
