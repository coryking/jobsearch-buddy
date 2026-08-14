# jobsearch-buddy

**The user is the human who installed the MCP server, not the LLM calling it.**
When analyzing usage, designing parameters, writing descriptions, or prioritizing
work — start from what the human was trying to do and whether they got a good
outcome. It's easy to look at how the LLM called us and ask "how can we make
this easier?" with an implicit "for the LLM." That optimizes the intermediary,
not the outcome. The LLM is infrastructure; the human searching for jobs is
the user.

## What This Project Is

A command-line tool and MCP server for job searching. The registered MCP
surface is **stateless live fetch**: `get_job` (any posting by URL or
company+id) and `list_company_jobs` (a whole board, compact rows) hit the ATS
at call time and never read the jobs table — plus OAuth-gated application
tracking, which is the stateful half. PostgreSQL holds the company registry
(the name→board phone book) and the activity log.

The corpus machinery — scrape-to-Postgres sync pipeline, FTS search over
LLM-distilled fields (`short_jd`, `description_normalized`, `salary`), hybrid
vector+FTS company search over researched bios — exists in the codebase and
the CLI can drive it, but its MCP tools (`mcp_tools/jobs.py`, `companies.py`,
`watchlists.py`) are deliberately unregistered and the sync timer is disabled
by the deploy. `mcp_tools/__init__.py` documents the one-line restore.

This is a practical tool, not enterprise software. Bias toward shipping.
80% today beats 99% tomorrow.

## Orient

Read [`docs/NORTH_STAR.md`](docs/NORTH_STAR.md) once per fresh context — it
holds the load-bearing design stance (the calling LLM is the ranker; jsb
provides evidence; NPOV; workplace-defining facts beat company-press-release
facts). Tool-description, distill-prompt, and bio-prompt work especially
should not start without it.

`.claude/rules/` carries the rules the harness picks up automatically:
[`session-start.md`](.claude/rules/session-start.md),
[`public-repo.md`](.claude/rules/public-repo.md),
[`handoff-docs.md`](.claude/rules/handoff-docs.md),
[`tdd-workflow.md`](.claude/rules/tdd-workflow.md),
[`migrations.md`](.claude/rules/migrations.md),
[`sync-pipeline.md`](.claude/rules/sync-pipeline.md),
[`infrastructure.md`](.claude/rules/infrastructure.md).

## Don't leak DB-derived company intel into public artifacts

The database holds posting-level data scraped from real companies, and the
operator typically uses it to study hiring patterns at companies they're
interested in (perennial reqs, GTM-vs-research mix, posting-age outliers,
named multi-year-old roles, etc.). That analysis is the operator's private
work product about real third parties.

GitHub issues, PRs, commit messages, branch names, and anything in `docs/` /
`README.md` / this file are public surfaces. Don't paste named-company
findings into them — even when the finding is the substantive answer to the
ticket. Public artifacts should describe the **class** of behavior:
"Greenhouse listings get touched within ~30d of today regardless of
`first_published`" — not "$Company has a 1,088-day-old req we can't
explain."

If you're working a ticket and the genuinely useful evidence is named-
company data, write the named version to a path *outside* this repo (e.g.
`~/notes/...`) and link it from chat to the operator. Keep the public
artifact at the class-of-behavior level. This applies to any
LLM/agent/contributor working in this repo, not just the original operator.

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
jsb check-boards [--ats X] [--company SLUG]... [--json]  # Live-probe every registered board; report ok/empty/error
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
| `sync_max_phase_cost_usd`  | `JOBBUDDY_SYNC_MAX_PHASE_COST_USD`   | `25.0` *(hard per-run spend ceiling; phase aborts past it)* |
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
