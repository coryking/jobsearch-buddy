---
description: Coding conventions for the jobbuddy package (MCP descriptions, formatting, error handling, dual interface)
globs: src/jobbuddy/**/*.py
---

# Coding Conventions

## MCP Description Writing

Tool descriptions in `mcp_server.py` are injected into the LLM's context — they're
routing hints, not API docs:

- **Server `instructions`**: Intent language ("find me jobs at...", "I applied for...").
  Name specific companies. Bias the LLM toward trying the tool first.
- **Tool docstrings**: Lead with *when to use*, not *what it does internally*.
- **Field `description`s**: Format hints, examples, valid values. Dense, not verbose.

## Human-Readable Formatting

Use the `humanize` library for formatting numbers, file sizes, time deltas, etc.
in user-facing output (CLI, Rich displays, logs). When touching code that
hand-rolls number formatting (e.g. `f"{n:,}"`, custom `_fmt_tokens()` helpers),
clean it up to use `humanize` instead.

## Error Handling

`core.py` raises `ValueError` on errors. CLI catches and exits, MCP catches and
returns error strings. Don't add try/except in core for expected error paths.

## Dual Interface Rule

New logic goes in `core.py` so both CLI and MCP get it. Typer/Rich deps stay
in `cli/`; FastMCP deps stay in `mcp_server.py`.

## Testing Philosophy

Test the store and sync layers — they handle data that's expensive to re-scrape
and easy to silently corrupt. Skip tests for CLI formatting, MCP descriptions,
and other presentation-layer stuff.
