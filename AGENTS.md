# jobsearch-buddy: Agent Guidelines

This is the canonical project context for AI agents working in this repository.
CLAUDE.md points here. Read this before working on code.

## What This Project Is

A command-line tool and MCP server for job searching: scrapes ATS job boards
(Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity, Workable, Eightfold),
caches listings in SQLite, and exposes them via a FastMCP server for use with
Claude Desktop or any MCP-compatible client.

It also serves as a **learning sandbox for ML/retrieval concepts** — embeddings,
vector search (sqlite-vec), fine-tuning, hybrid search. Architecture should
support both stable tool use and experimentation.

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Package Structure

```
src/jobbuddy/
├── cli.py          # Typer CLI (ats command)
├── mcp_server.py   # FastMCP server (ats-mcp command)
├── core.py         # Shared logic: fetch, save, sync, URL parsing
├── cache.py        # SQLite cache layer (WAL mode, sqlite-vec for vector search)
├── embeddings.py   # fastembed model (Snowflake arctic-embed-l, 1024 dims)
├── settings.py     # pydantic-settings config (env vars, platformdirs paths)
├── registry.py     # Company registry + fuzzy name matching
├── models.py       # Pydantic Job, FetchResult, Company models
├── url.py          # ATS URL parser
├── job_log.py      # CSV activity log (WA unemployment audit compliance)
├── companies.json  # Company registry data
└── fetchers/       # Per-ATS-platform scrapers (strategy pattern)
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
├── test_cache.py       # Cache layer: upsert, dedup, soft-delete, vector search
├── test_embeddings.py  # Embedding module: embed_text, serialize_f32, model
├── test_sync.py        # Sync orchestration: callbacks, error isolation, staleness
└── test_settings.py    # Settings: defaults, env var overrides, singleton

docs/
├── architecture.md     # Detailed architecture, cache design, fetcher pattern
└── data-format.md      # Job search CSV log format
```

## Build / Test Commands

```bash
uv sync                          # Install dependencies
uv run python -m pytest tests/ -v  # Run tests
ats --help                       # CLI help
ats-mcp                          # Run MCP server
```

## CLI Commands

```
ats sync [--company NAME] [--stale HOURS]   # Sync ATS boards into cache
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

| Setting           | Env Var              | Default                                   |
|-------------------|----------------------|-------------------------------------------|
| `data_dir`        | `JOBBUDDY_DATA_DIR`  | `~/projects/resume/data`                  |
| `db_path`         | `JOBBUDDY_DB_PATH`   | platformdirs `user_data_dir/jobs_cache.db`|
| `listings_dir`    | `JOBBUDDY_LISTINGS_DIR` | `~/projects/resume/job-listings`       |

## Architecture Conventions

- `core.py` raises `ValueError`; callers (CLI, MCP) handle presentation
- CLI and MCP both call into `core.py` — shared logic, dual interface
- All search reads from SQLite cache; only `ats sync` touches the network
- Tests use `:memory:` SQLite or `tmp_path` — fast, no real DB or network

See `docs/architecture.md` for detailed architecture documentation.
See `src/jobbuddy/CLAUDE.md` for package-specific coding guidance.
