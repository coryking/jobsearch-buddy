# Project TODO

Items deferred from current work that need attention.

## Account ID for Job Search Log

The job search activity log (CSV) has no concept of which user performed
the action. For multi-tenant / public MCP server use, every log entry
needs an `account_id` (or `user_id`) column so entries are scoped per user.

**What needs to change:**
- `job_log.py`: `append_row()` and `read_log()` need account_id parameter
- `mcp_server.py`: Extract GitHub username from `X-MS-CLIENT-PRINCIPAL-NAME`
  header and pass to log functions
- Migration: Add column to existing log data (backfill with "cory" or similar)
- Decide: keep CSV or migrate log to PostgreSQL (probably Postgres, since
  the log needs per-user queries and CSV doesn't scale for multi-tenant)

**Blocked by:** Azure migration PoC validation (need Easy Auth working first
to confirm how user identity flows through)
