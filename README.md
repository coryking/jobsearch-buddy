# jobsearch-buddy

A CLI tool and cloud-hosted MCP server for job searching. Scrapes job listings from 100+ company ATS boards (Greenhouse, Ashby, Lever, Workday, and others), caches them in PostgreSQL with pgvector, and supports semantic search via OpenAI-compatible embeddings. The MCP server runs on Azure Functions so Claude can search jobs from anywhere.

Built for personal use during a job search, evolving toward something more polished. Not enterprise software yet.

## Quick Start

Requires PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension.

```bash
git clone https://github.com/coryking/jobsearch-buddy
cd jobsearch-buddy
cp .env.example .env              # Configure your database connection
uv sync
jsb migrate                       # Create tables + pgvector indexes
jsb sync fetch enrich             # Scrape all registered ATS boards (no API key needed)
jsb search --title "engineer" --location "Seattle"
```

## What You Get Without an API Key

- Scrape 100+ company job boards across 9 ATS platforms (Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity, Workable, Eightfold, Oracle HCM)
- Keyword search across all cached listings
- Application logging with CSV export (WA state unemployment audit compliance)
- MCP server for use with Claude Desktop or any MCP-compatible client

## What You Get With an OpenAI-Compatible API Key

Set `JOBBUDDY_OPENAI_API_KEY` in your `.env` file or environment. Works with OpenAI, Azure OpenAI, or any compatible provider (Ollama, Together, etc.).

- LLM-powered description cleaning (strips boilerplate like EEO statements, generic company blurbs)
- Vector embeddings + semantic search ("find me ML roles with good parental leave")
- Web search UI (`jsb serve`)

Run the full sync pipeline with all four phases:

```bash
jsb sync                          # fetch → enrich → strip → embed
```

## Cloud-Hosted MCP Server

The MCP server is deployed on Azure Functions (Flex Consumption) with Entra ID authentication. This lets Claude (mobile, web, desktop) search jobs and log applications without a local install.

The cloud deployment uses:
- **Azure Functions** (Flex Consumption) running FastMCP with streamable HTTP
- **Azure PostgreSQL** (Flexible Server) with pgvector for job data
- **Azure Managed Redis** for OAuth state (DCR registrations, auth codes)
- **Entra ID** for authentication (managed identity for service-to-service, OAuth for clients)

Infrastructure is managed with Terraform in `infrastructure/`. The server is not publicly available yet — access is restricted to the owner's Entra tenant.

See `docs/azure-migration.md` for architecture details.

## Application Log

Job applications and activities are stored per-account in PostgreSQL,
designed for WA state unemployment audit compliance. Every row is owned
by the authenticated MCP account that wrote it — there is no
unauthenticated write path.

The MCP server exposes three tools for application tracking, all of
which require an authenticated session:

- `log_job_application` — record an application (URL or company+job_id)
- `log_job_activity` — log freeform activity (recruiter call, interview,
  referral) where there's no ATS job_id to attach
- `review_activity_log` — read history per company or summarized across
  all companies

## CLI Commands

```
jsb migrate                                  Apply pending database migrations
jsb sync [PHASES...] [--company NAME] [--stale HOURS]  Sync pipeline (phases: fetch, enrich, strip, embed)
jsb list-jobs [company] [-f FILTER]         List cached jobs, optionally filtered
jsb search [--title T] [--location L]       Keyword search across cached listings
jsb companies                               List registered companies
jsb save <company> <job_ids...> [-o DIR]    Save job listings as markdown files
jsb lookup <url>                            Fetch and display a single job listing
jsb log <url> [-a ACTION] [-p PERSON]       Log a job application or activity
jsb serve                                   Start the web search UI
jsb embed-test QUERIES... [-f FILE]         Pure embedding similarity test (no DB)
jsb-mcp                                     Run the MCP server (local, stdio)
jsb-eval                                    Run the strip prompt eval framework
```

## MCP Server

### Local (stdio)

For Claude Desktop, add to your config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "job-search": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jobsearch-buddy", "jsb-mcp"]
    }
  }
}
```

For Claude Code:

```bash
claude mcp add job-search -- uv run --directory /path/to/jobsearch-buddy jsb-mcp
```

### Cloud (streamable HTTP)

The cloud-hosted MCP server uses Entra ID OAuth with Dynamic Client Registration. Claude's MCP connector handles the auth flow automatically — just add the server URL.

The MCP server exposes these tools: `search_jobs`, `semantic_search_jobs`, `get_job_post_details`, `log_job_application`, `log_job_activity`, `review_activity_log`.

## Configuration

All settings are managed via environment variables (prefix `JOBBUDDY_`) or a `.env` file. Copy `.env.example` to `.env` to get started.

### Database

Connect via `pg_service.conf` (recommended) or explicit connection parameters:

| Setting              | Env Var                          | Default                        |
|----------------------|----------------------------------|--------------------------------|
| `pg_service`         | `JOBBUDDY_PG_SERVICE`            | `job-search-buddy-azure`       |
| `postgres_host`      | `JOBBUDDY_POSTGRES_HOST`         | `None` *(enables Entra auth)*  |
| `postgres_database`  | `JOBBUDDY_POSTGRES_DATABASE`     | `None`                         |
| `postgres_user`      | `JOBBUDDY_POSTGRES_USER`         | `None` *(managed identity)*    |

### Paths

| Setting        | Env Var                  | Default                                |
|----------------|--------------------------|----------------------------------------|
| `data_dir`     | `JOBBUDDY_DATA_DIR`      | platformdirs `user_data_dir` / `data`  |
| `listings_dir` | `JOBBUDDY_LISTINGS_DIR`  | platformdirs `user_data_dir` / `listings` |

### OpenAI / LLM

| Setting                    | Env Var                              | Default                    |
|----------------------------|--------------------------------------|----------------------------|
| `openai_api_key`           | `JOBBUDDY_OPENAI_API_KEY`            | `None` *(enables strip/embed/search)* |
| `openai_base_url`          | `JOBBUDDY_OPENAI_BASE_URL`           | `None` *(omit for api.openai.com)*    |
| `openai_azure_api_version` | `JOBBUDDY_OPENAI_AZURE_API_VERSION`  | `None` *(if set, uses AzureOpenAI)*   |
| `strip_model`              | `JOBBUDDY_STRIP_MODEL`               | `gpt-5-nano`               |
| `embedding_model`          | `JOBBUDDY_EMBEDDING_MODEL`           | `text-embedding-3-small`   |

## Supported ATS Platforms

| Platform   | URL Pattern                                                    |
|------------|----------------------------------------------------------------|
| Greenhouse | `boards.greenhouse.io/{board}/jobs/{id}`                       |
| Ashby      | `jobs.ashbyhq.com/{board}/{uuid}`                              |
| Lever      | `jobs.lever.co/{company}/{uuid}`                               |
| Workday    | `{company}.wd{N}.myworkdayjobs.com/...`                       |
| Rippling   | `ats.rippling.com/{company}/jobs/{uuid}`                       |
| Workable   | `apply.workable.com/{board}/j/{shortcode}`                     |
| Paylocity  | `recruiting.paylocity.com/Recruiting/Jobs/...`                 |
| Eightfold  | various (e.g. Microsoft)                                       |
| Oracle HCM | `{tenant}.fa.{region}.oraclecloud.com/.../sites/{site}/job/{id}` |

## Eval Framework

A distill prompt eval framework (`jsb-eval`) exists for iterating on the LLM distill prompt. It runs candidate prompts against sample job descriptions and uses an LLM judge to score recall, precision, integrity, and fidelity. See `eval/CLAUDE.md` for details.

## Project Structure

```
src/jobbuddy/
  cli/           Typer CLI, split into sync/search/jobs/log/migrate submodules
  sync/          Sync pipeline: fetch, enrich, strip, embed phases
  fetchers/      Per-ATS-platform scrapers (strategy pattern)
  mcp_server.py  FastMCP server (local stdio + cloud HTTP)
  core.py        Shared logic for CLI and MCP
  store.py       PostgreSQL + pgvector storage layer
  search.py      Vector search (HNSW via pgvector)
  embeddings.py  OpenAI-compatible embedding client
  settings.py    pydantic-settings configuration
  registry.py    Company registry + fuzzy matching
  models.py      Pydantic data models
  web.py         Flask web search UI

infrastructure/  Terraform for Azure deployment
docs/            Architecture docs, migration notes
```

See `CLAUDE.md` for architecture details.

## License

GPLv2
