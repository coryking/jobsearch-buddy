---
description: Schema migration policy — explicit and manual via `jsb migrate`, never auto-applied
globs: src/jobbuddy/migrations/*.sql, src/jobbuddy/migrate.py
---

# Schema Migrations

Migrations are **explicit and manual** — run `jsb migrate` to apply them.
`JobStore` does not auto-migrate on connection. This prevents accidental
schema changes from scripts, MCP servers, or other code that instantiates
a `JobStore`.

Migration files live in `src/jobbuddy/migrations/` as numbered SQL files
(e.g. `001_initial.sql`). The `schema_migrations` table tracks which have
been applied. After adding a new migration file, run `jsb migrate` to
apply it.

Tests apply migrations once per session via the `ensure_pg_schema` fixture
in `tests/conftest.py`.
