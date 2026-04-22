---
title: MCP `search_jobs` Ergonomics — Quick-Win Bundle
date: 2026-04-15
status: requirements
scope: lightweight
---

# MCP `search_jobs` Ergonomics — Quick-Win Bundle

## Problem

An LLM client running a single user request (“find me dope-ass AI SWE jobs from
startups”) issued ~13 calls to `search_jobs` in one session and hit the
“Too many results (>100)” overflow error 4 times. The root causes are in the
tool’s ergonomics, not the underlying data:

1. The overflow error tells the LLM to “narrow it down” but gives no signal
   about *what dimension* is dominating the result set. The LLM guesses
   narrower filters blindly.
2. There is no first-class “startup vs enterprise” signal in results, so the
   LLM has to rely on its own world knowledge to filter out corporate hits
   (Metropolis, CoreWeave, HP). That knowledge drifts across models and runs.
3. `title_filter` and `location_filter` accept CSV for OR-matching, but
   `company` is single-valued. Asking “anything at my top-5 dream companies?”
   requires N serial calls.
4. There is no way to say “skip things I’ve already applied to” or “skip
   these companies, I already saw them in the previous search,” so the LLM
   keeps re-surfacing the same dominant jobs across overlapping queries.

Semantic-search improvements and company-profile-driven embeddings are
explicitly out of scope here — this is a filter-ergonomics pass.

## Goals

- Reduce the number of MCP calls the LLM needs to fulfill a typical
  cross-company job discovery request.
- When the tool refuses a query as too broad, give the LLM enough information
  to narrow productively on the first retry.
- Let a single `search_jobs` call cover a user’s target-company list.
- Let the LLM prune results the user has already seen or acted on.

## Non-Goals

- No changes to the semantic search pipeline, embedding generation, or strip
  phase.
- No new data about companies themselves (stage tags / profiles are
  a separate brainstorm — see open question below).
- No changes to `get_job_post_details`, `log_job_application`, or
  `review_activity_log`.
- No session-level state on the MCP server (stateless per call stays).

## Scope: the Bundle

Three changes, all in `search_jobs` (`src/jobbuddy/mcp_server.py`) and
`VectorSearch.search` / `JobStore.query_jobs` / `JobStore.search_similar_filtered`
(`src/jobbuddy/search.py`, `src/jobbuddy/store.py`).

### A. Facet breakdown on overflow

When the candidate result set would exceed `max_results` (currently 100),
replace the current generic error with an overflow response that includes
facet counts over the *filtered* candidate set:

- Top 10 **companies** with counts
- Top 10 **locations** with counts
- For the **non-semantic path**: total matching-rows count (from a
  `COUNT(*)` over the same filter predicate)
- For the **semantic path**: no total — only the top-K facet distribution.
  The HNSW top-`max_results+1` sentinel doesn't give a meaningful total
  without a separate non-vector query, which isn't worth the extra round-trip.

Format: compact plain-text bullet blocks (not CSV, not JSON) so an LLM can
scan and pick a narrower filter on retry. The existing “add these filters”
hint can stay as a short tail.

**Title-prefix facets are deliberately NOT in v1.** Ship company + location
facets only. If a replay of the motivating query still shows the LLM unable
to narrow after that, revisit and add a title-prefix facet. Keeps v1 from
bikeshedding on prefix extraction rules.

Implementation note — two branches:
- **Non-semantic path** (no `query`): facets come from additional
  `GROUP BY … ORDER BY count DESC LIMIT 10` queries against the same filter
  predicate, in the same round-trip where possible, plus a `COUNT(*)` for
  the total.
- **Semantic path** (has `query`): facets are aggregated in Python over the
  HNSW top-`max_results+1` rows already returned by
  `search_similar_filtered`. Do not run a separate SQL GROUP BY for the
  semantic path — that would describe the cache, not the query.

Acceptance criteria:
- Overflow response contains company + location facet blocks with integer
  counts.
- Non-semantic overflow response includes a total matching-rows count;
  semantic overflow response does not.
- Facets reflect the current filter predicate (title, location, since,
  company, semantic query) *and* the exclusion filters from Section C
  (`exclude_company`, `exclude_applied`) — counts are over the
  post-exclusion candidate set.
- If a facet has fewer than 10 distinct values, show all of them.

### B. Multi-valued `company` filter

Change `company` param on `search_jobs` to accept a comma-separated list,
matching the pattern used by `title_filter` and `location_filter`. Each entry
is resolved independently via `lookup_by_name`; unknown names still produce
an error but identify which name failed.

Acceptance criteria:
- `company="anthropic,vercel,cursor"` returns jobs from any of the three.
- Unknown company in the list: error names the specific unknown entry.
- Single-company calls behave exactly as before (backward compatible).
- The overflow response prose lists the resolved company filter
  (e.g. `company filter: anthropic,vercel,cursor`) instead of a single slug.

### C. `exclude_company` and `exclude_applied` filters

Two new optional params on `search_jobs`:

- `exclude_company` (string, default ""): CSV of company names/slugs to
  exclude from results. Same resolution rules as `company`. Pushed into SQL
  as an `AND company_slug <> ALL(%s)` predicate — no post-fetch trimming.
- `exclude_applied` (bool, default `false`): when true, drop any job that
  already appears in the application log. This is the same cross-ref used
  today to mark results as `applied=yes`; we just let the LLM filter on it.

Both exclusions are applied **in SQL**, not in Python post-fetch. The
application log is a CSV file, not a Postgres table, so the flow for
`exclude_applied` is:

1. `read_log()` in Python (already called in the current search path to
   populate the displayed `applied` column — one call, not two).
2. Extract two exclusion sets: (a) job_ids with a company context, and
   (b) (company_slug, lowercase role) tuples for log rows missing a job_id.
3. Pass both as parameter arrays to the store; add `AND NOT (...)`
   predicates to the existing WHERE clause alongside the main filter
   predicate.

This keeps the overflow check honest — `limit=max_results+1` runs against
the already-excluded candidate set, so excluding known-seen companies can
genuinely unblock an otherwise-too-broad query without over-fetch hacks.

Acceptance criteria:
- `exclude_company="scale ai,microsoft"` returns zero hits from either.
- `exclude_applied=true` drops any job with a non-empty `applied` field —
  i.e. any job that already appears in the activity log via the same
  cross-ref that populates the displayed `applied` column
  (`models.py` `JobSearchResults.from_query`).
- `exclude_applied` inherits the match rules of the existing cross-ref:
  job_id match first, with fallback to case-insensitive (company, role)
  equality. Jobs logged via `log_job_activity` with no `job_id` and a
  differently-worded role will not be excluded. Call this out in the
  `exclude_applied` MCP field description so the LLM understands the
  limitation.
- Both filters apply *after* the main filter/query predicate but *before*
  the overflow check, so excluding known-seen companies can unblock an
  otherwise-too-broad query.

## Tool Description Updates

Update `search_jobs` docstring and field descriptions to teach the LLM
about the new ergonomics:

- Mention that `company` now accepts CSV.
- Mention `exclude_company` / `exclude_applied` as ways to prune seen results
  across a session.
- Reframe the overflow behavior: “If too many matches, the tool returns a
  facet breakdown — use it to narrow intelligently.”

MCP descriptions are routing hints, per `.claude/rules/coding-conventions.md`.
Keep additions dense, not verbose.

## Testing Strategy

Per `.claude/rules/tdd-workflow.md`:

- **TDD**: store/sync boundary only — new/updated `JobStore` method(s) for
  facet aggregation, and the `exclude_company` / `exclude_applied`
  predicates on `query_jobs` and `search_similar_filtered`.
- **Skip TDD** for MCP tool description wording and overflow-response
  string formatting (presentation layer, per coding conventions).
- Existing tests in `tests/test_store.py` and `tests/test_search.py` must
  still pass unchanged.

Add tests for:
- Facet aggregation returns top-N groupings respecting filters.
- `company` param accepts CSV and resolves each entry.
- `exclude_company` removes matching rows.
- `exclude_applied=true` cross-refs the log and prunes correctly.

## Risks and Open Questions

1. **Facet cost on semantic queries.** HNSW top-K is already being computed;
   facets run over the returned rows, so cost is negligible. If we ever want
   facets over the *full* semantic candidate set, that’s a different query.
2. **Log cross-ref performance for `exclude_applied`.** The log is a CSV
   loaded into memory in `read_log()`. At current scale (hundreds of rows)
   this is fine. Flag for revisit if the log ever grows past a few thousand.
3. **Open for later**: company `stage` tag (startup / scaleup / enterprise /
   bigco) remains the most direct fix for “find me startups,” but requires
   a data model change and manual tagging pass. Deferred to a separate
   brainstorm — likely bundled with the Azure-credit-based company profile
   work.

## Handoff

This document defines *what* changes. Implementation plan (file-level
changes, test ordering, store method shapes) is the job of `/ce:plan`
or direct TDD implementation, not this brainstorm.
