# jobsearch-buddy

A CLI tool and MCP server for job searching. Scrapes ATS job boards, caches
listings locally, and provides search, filtering, and application logging.

Also a learning sandbox for ML/retrieval concepts: embeddings, vector search,
fine-tuning, hybrid search.

## Install

```bash
git clone https://github.com/coryking/jobsearch-buddy
cd jobsearch-buddy
uv sync
uv pip install -e .
```

## Usage

```bash
# Sync job listings from all registered companies
ats sync

# Search cached jobs
ats search --title "staff engineer" --location "Seattle"

# List jobs for a specific company
ats list-jobs microsoft

# Fetch and save a specific job listing
ats lookup https://boards.greenhouse.io/acme/jobs/12345

# Log a job application
ats log https://boards.greenhouse.io/acme/jobs/12345 -a Application
```

## MCP Server

Add to Claude Desktop config:

```json
{
  "mcpServers": {
    "job-search": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jobsearch-buddy", "ats-mcp"]
    }
  }
}
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

**Minimal (fetch + enrich only):** No API key needed. `ats sync` scrapes job
boards and caches listings locally.

**Full features (strip + embed + semantic search):** Set `JOBBUDDY_OPENAI_API_KEY`.
Works with any OpenAI-compatible API:

```bash
# Standard OpenAI
JOBBUDDY_OPENAI_API_KEY=sk-...

# Azure OpenAI
JOBBUDDY_OPENAI_API_KEY=your-key
JOBBUDDY_OPENAI_BASE_URL=https://your-resource.openai.azure.com/
JOBBUDDY_OPENAI_AZURE_API_VERSION=2024-12-01-preview

# Other providers (Groq, Together, Ollama, etc.)
JOBBUDDY_OPENAI_API_KEY=your-key
JOBBUDDY_OPENAI_BASE_URL=http://localhost:11434/v1
```

## Project Structure

See `AGENTS.md` for architecture and `docs/architecture.md` for details.

## Supported ATS Platforms

Greenhouse, Ashby, Lever, Workday, Rippling, Workable, Paylocity, Eightfold
