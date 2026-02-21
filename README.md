# jobsearch-buddy

A CLI tool that scrapes job listings from 100+ company ATS boards (Greenhouse, Ashby, Lever, Workday, and others), caches them in SQLite, and supports semantic search via OpenAI-compatible embeddings. Built for personal use during a job search, shared because it might be useful to others. Not enterprise software.

## Quick Start

```bash
git clone https://github.com/coryking/jobsearch-buddy
cd jobsearch-buddy
uv sync
jsb sync              # Scrape all registered company job boards
jsb search --title "engineer" --location "Seattle"
```

## What You Get Without an API Key

- Scrape 100+ company job boards across 8 ATS platforms (Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity, Workable, Eightfold)
- Keyword search across all cached listings
- Application logging with CSV export (WA state unemployment audit compliance)
- MCP server for use with Claude Desktop or any MCP-compatible client

## What You Get With an OpenAI-Compatible API Key

Set `JOBBUDDY_OPENAI_API_KEY` in your `.env` file or environment. Works with OpenAI, Azure OpenAI, or any compatible provider (Ollama, Together, etc.).

- LLM-powered description cleaning (strips boilerplate like EEO statements, generic company blurbs)
- Vector embeddings + semantic search ("find me ML roles with good parental leave")
- Web search UI (`jsb serve`)

## CLI Commands

```
jsb sync [--company NAME] [--stale HOURS]   Sync ATS boards into local cache
jsb strip [--force]                         Strip boilerplate from descriptions (requires API key)
jsb embed                                   Generate embeddings for search (requires API key)
jsb list-jobs [company] [-f FILTER]         List cached jobs, optionally filtered
jsb search [--title T] [--location L]       Keyword search across cached listings
jsb companies                               List registered companies
jsb save <company> <job_ids...> [-o DIR]    Save job listings as markdown files
jsb lookup <url>                            Fetch and display a single job listing
jsb log <url> [-a ACTION] [-p PERSON]       Log a job application or activity
jsb serve                                   Start the web search UI
jsb-mcp                                     Run the MCP server
jsb-eval                                    Run the strip prompt eval framework
```

## MCP Server

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

The MCP server exposes these tools: `search_jobs`, `semantic_search_jobs`, `get_job_post_details`, `log_job_application`, `log_job_activity`, `review_activity_log`.

## Configuration

All settings are managed via environment variables (prefix `JOBBUDDY_`) or a `.env` file. Copy `.env.example` to `.env` to get started.

| Setting                    | Env Var                              | Default                                          |
|----------------------------|--------------------------------------|--------------------------------------------------|
| `data_dir`                 | `JOBBUDDY_DATA_DIR`                  | platformdirs `user_data_dir` / `data`            |
| `db_path`                  | `JOBBUDDY_DB_PATH`                   | platformdirs `user_data_dir` / `jobs_cache.db`   |
| `listings_dir`             | `JOBBUDDY_LISTINGS_DIR`              | platformdirs `user_data_dir` / `listings`        |
| `openai_api_key`           | `JOBBUDDY_OPENAI_API_KEY`            | `None` (enables strip/embed/search)              |
| `openai_base_url`          | `JOBBUDDY_OPENAI_BASE_URL`           | `None` (omit for api.openai.com)                 |
| `openai_azure_api_version` | `JOBBUDDY_OPENAI_AZURE_API_VERSION`  | `None` (if set, uses AzureOpenAI client)         |
| `strip_model`              | `JOBBUDDY_STRIP_MODEL`               | `gpt-5-nano`                                     |
| `embedding_model`          | `JOBBUDDY_EMBEDDING_MODEL`           | `text-embedding-3-small`                         |

## Supported ATS Platforms

| Platform   | URL Pattern                                    |
|------------|------------------------------------------------|
| Greenhouse | `boards.greenhouse.io/{board}/jobs/{id}`       |
| Ashby      | `jobs.ashbyhq.com/{board}/{uuid}`              |
| Lever      | `jobs.lever.co/{company}/{uuid}`               |
| Workday    | `{company}.wd{N}.myworkdayjobs.com/...`       |
| Rippling   | `ats.rippling.com/{company}/jobs/{uuid}`       |
| Workable   | `apply.workable.com/{board}/j/{shortcode}`     |
| Paylocity  | `recruiting.paylocity.com/Recruiting/Jobs/...` |
| Eightfold  | various (e.g. Microsoft)                       |

## Eval Framework

A strip prompt eval framework (`jsb-eval`) exists for iterating on the LLM boilerplate removal prompt. It runs candidate prompts against sample job descriptions and uses an LLM judge to score recall, precision, integrity, and fidelity. See `eval/AGENTS.md` for details.

## Project Structure

```
src/jobbuddy/
  cli/           Typer CLI, split into sync/search/jobs/log submodules
  sync/          Sync pipeline: fetch, enrich, strip, embed phases
  fetchers/      Per-ATS-platform scrapers (strategy pattern)
  mcp_server.py  FastMCP server
  core.py        Shared logic for CLI and MCP
  store.py       SQLite + sqlite-vec storage layer
  search.py      Vector search (KNN via sqlite-vec)
  embeddings.py  OpenAI-compatible embedding client
  settings.py    pydantic-settings configuration
  registry.py    Company registry + fuzzy matching
  models.py      Pydantic data models
  web.py         Flask web search UI
```

See `AGENTS.md` for architecture details.

## License

GPLv2
