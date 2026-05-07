# jobsearch-buddy

**The user is the human who installed the MCP server, not the LLM calling it.**
When analyzing usage, designing parameters, writing descriptions, or prioritizing
work — start from what the human was trying to do and whether they got a good
outcome. It's easy to look at how the LLM called us and ask "how can we make
this easier?" with an implicit "for the LLM." That optimizes the intermediary,
not the outcome. The LLM is infrastructure; the human searching for jobs is
the user.

## What This Project Is

A command-line tool and MCP server for job searching: scrapes ATS job boards
(see Supported ATS Platforms below), stores listings in PostgreSQL, and exposes
them via a FastMCP server for use with Claude Desktop or any MCP-compatible
client. Job search uses PostgreSQL full-text search over a per-job distill
pipeline that produces `short_jd`, `description_normalized`, and `salary`.
Company search (`find_companies`) uses hybrid vector + FTS over researched
company bios, fused with reciprocal rank fusion.

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Package Layout

Code lives under `src/jobbuddy/`. Things that aren't obvious from `ls`:

- `core.py` is the shared layer between CLI and MCP. Both interfaces import
  from it; new business logic goes here, not in `cli/` or `mcp_server.py`.
- `cli/` is a package, not a single file: each submodule registers its
  commands on the shared `app` Typer instance via `@app.command()`.
- `sync/` is a phase pipeline; phases extend the `WorkerPhase` ABC in
  `sync/base.py`. The orchestrator lives in `sync/orchestrator.py`;
  `sync/__init__.py` re-exports the public surface.
- `fetchers/` is one module per ATS platform plus an `ATSFetcher` ABC and a
  registry/factory in `fetchers/__init__.py`. Add new ATSes here.
- `research.py` runs the Azure Responses API + `web_search` to populate
  `companies.short_bio` and `long_bio`. The bio feeds the distill prompt.
- `migrations/` holds numbered SQL files applied by `jsb migrate`.

`tests/` mirrors the package — `test_store.py`, `test_sync.py`, plus per-ATS
fetcher tests. `docs/architecture.md` is the long-form architecture; this
file is the navigation layer.

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
jsb sync [PHASES...] [--company NAME] [--stale HOURS]  # Sync pipeline (phases: fetch, enrich, research, distill)
jsb find-companies "<query>" [--limit N]    # Hybrid vector+FTS search over company bios
jsb research-companies [--company NAME]... [--force]  # Fill companies.short_bio/long_bio (Azure Responses + web_search)
jsb list-jobs [company] [-f FILTER]         # List jobs
jsb search [--query Q] [--posted-since S] [--limit N]  # FTS search across stored jobs
jsb companies                               # List registered companies
jsb save <company> <job_ids...> [-o DIR]    # Save listings as markdown
jsb lookup <url>                            # Fetch single job details
```

Application logging and review (`log_job_application`,
`log_job_activity`, `review_activity_log`) are MCP-only — every row in
`activity_log` is owned by an authenticated account, so both the write
and read paths are gated behind a verified OAuth token.

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
| `distill_model`            | `JOBBUDDY_DISTILL_MODEL`             | `gpt-5.4-nano`                             |
| `distill_reasoning_effort` | `JOBBUDDY_DISTILL_REASONING_EFFORT`  | `high`                                     |
| `distill_prompt_version`   | `JOBBUDDY_DISTILL_PROMPT_VERSION`    | `distill-v3.1`                             |
| `research_model`           | `JOBBUDDY_RESEARCH_MODEL`            | `gpt-5.4`                                  |
| `research_endpoint`        | `JOBBUDDY_RESEARCH_ENDPOINT`         | `None` *(Azure OpenAI resource root URL)*  |
| `research_max_workers`     | `JOBBUDDY_RESEARCH_MAX_WORKERS`      | `4`                                        |
| `embedding_model`          | `JOBBUDDY_EMBEDDING_MODEL`           | `text-embedding-3-small`                   |

## Architecture Conventions

- `core.py` raises `ValueError`; callers (CLI, MCP) handle presentation
- CLI and MCP both call into `core.py` — shared logic, dual interface
- Migrations are explicit — `JobStore` never auto-migrates
- Tests use a dedicated test PostgreSQL database — fast, no network

See `docs/architecture.md` for detailed architecture documentation.
See `src/jobbuddy/CLAUDE.md` for package-specific coding guidance.
