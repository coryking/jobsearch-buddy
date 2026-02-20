# jobbuddy Package

See `AGENTS.md` in the repo root for project overview, build commands, and
architecture index. See `docs/architecture.md` for detailed architecture.

## Package-Specific Coding Conventions

### MCP Description Writing

Tool descriptions in `mcp_server.py` are injected into the LLM's context — they're
routing hints, not API docs:

- **Server `instructions`**: Intent language ("find me jobs at...", "I applied for...").
  Name specific companies. Bias the LLM toward trying the tool first.
- **Tool docstrings**: Lead with *when to use*, not *what it does internally*.
- **Field `description`s**: Format hints, examples, valid values. Dense, not verbose.

### Human-Readable Formatting

Use the `humanize` library for formatting numbers, file sizes, time deltas, etc.
in user-facing output (CLI, Rich displays, logs). When touching code that
hand-rolls number formatting (e.g. `f"{n:,}"`, custom `_fmt_tokens()` helpers),
clean it up to use `humanize` instead. Common functions:

- `humanize.intcomma(n)` — `1234567` → `"1,234,567"` (replaces `f"{n:,}"`)
- `humanize.metric(n)` — `12345` → `"12.3k"` (compact display in dashboards)
- `humanize.naturalsize(n)` — bytes → `"1.2 MB"`
- `humanize.naturaldelta(seconds)` — `3661` → `"an hour"`

### Error Handling

`core.py` raises `ValueError` on errors. CLI catches and exits, MCP catches and
returns error strings. Don't add try/except in core for expected error paths.

### Dual Interface Rule

New logic goes in `core.py` so both CLI and MCP get it. Typer/Rich deps stay
in `cli/`; FastMCP deps stay in `mcp_server.py`.

### Testing Philosophy

Test the store and sync layers — they handle data that's expensive to re-scrape
and easy to silently corrupt. Skip tests for CLI formatting, MCP descriptions,
and other presentation-layer stuff.

```bash
uv run python -m pytest tests/ -v
```

### Adding a New Fetcher

1. Create `fetchers/{platform}.py` inheriting `ATSFetcher`
2. Implement `list_jobs()` → `list[Job]` and `fetch_job()` → `Job`
3. Register in `fetchers/__init__.py` `FETCHER_MAP` and `SUPPORTED_ATS_TYPES`
4. Add a test entry to `companies.json`

Decide: full fetcher (returns descriptions in `list_jobs()`) or stub (returns
stubs, enrichment fetches descriptions individually). Full is preferred when
the ATS API supports it.
