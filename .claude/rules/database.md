---
description: PostgreSQL connection — where the database is, how to connect from any box
globs: src/jobbuddy/store.py, src/jobbuddy/settings.py, tests/conftest.py, src/jobbuddy/cli/migrate.py
---

# Database

PostgreSQL on **devbox** (`devbox.sapsucker-chromatic.ts.net`), database `jobsearchbuddy`,
role `coryk`. Auth is `.pgpass` (password), not Entra tokens.

Connection goes through `pg_service.conf` (chezmoi-managed, same file on every box):

| Entry | Host | Use |
|---|---|---|
| `jobsearchbuddy` | `localhost` | On devbox (where PG runs). Code default. |
| `jobsearchbuddy-remote` | `devbox.sapsucker-chromatic.ts.net` | From wigglebutt/Mac — same DB over tailnet. |
| `jobsearchbuddy-test` | `devbox.sapsucker-chromatic.ts.net` | Test database (`job_search_buddy_test`). |

**If you are on wigglebutt**, set `JOBBUDDY_PG_SERVICE=jobsearchbuddy-remote` or
use `PGSERVICE=jobsearchbuddy-remote psql` for ad-hoc queries.

The MCP server runs on devbox and uses the default (`jobsearchbuddy` → localhost).

`settings.py` exposes `pg_conninfo` as `service={pg_service}` — no token, no URI
construction. If you find yourself building a connection string by hand or fetching
Azure tokens for PG, something is wrong.
