# dream.md

A scheduled task fires this file. The trigger is dumb and durable:

> read `claude-workspace/dream.md` and do what it says

**Before anything else, check for `dream-invariants.md` in this directory. If it exists, read it first and obey it.** That file is Cory's; this file is mine. Conflicts resolve in favor of invariants.

## Purpose (not prescription)

The dream serves jobsearch-buddy's core mission: be the best evidence provider that lets Cory (the human searching for a job) see more good matches and fewer noise. The daily `jsb-sync` already runs scraping and distill. This routine's job is **not** to do what jsb-sync does — it's to watch the system from above: catch silent breakage, surface patterns a single session can't see, and prepare context for the next working session.

**What "useful output" looks like is for the dream to discover.** A sync-health briefing, a MCP tool quality note, a `smell` issue — different shapes earn their keep in different runs. Don't lock in an answer; evolve.

**Working hypothesis (mutable):** a state-of-jsb briefing at `claude-workspace/observations/state-of-jsb.md`, leading with sync health, then data quality signals, then what Cory should look at first — is the plausible headline output shape. Try it. If Cory engages with it, invest; if it goes ignored, change the shape.

**NORTH_STAR constraint:** every output should be checkable against `docs/NORTH_STAR.md`. If a finding or fix optimizes the LLM intermediary at the expense of the human user's outcomes, it's the wrong direction.

## The protocol

Five phases. Order is load-bearing; shape within each phase is mine to evolve.

### Phase 1 — Orient

Read what's accumulated since last run, in parallel where calls don't depend on each other:

- **Sync health (primary signal).** `psql "service=mozicode-devbox"` (or whatever the jsb DB connection is — check `.env` or `docs/` for the service name). Query: most recent 20 sync_runs grouped by status and store. New failure clusters = headline finding. If a store is failing 3+ runs in a row, that's a scraper regression.
- **Project state.** `git log --oneline -20 main`, `gh issue list --state open`, `gh run list --branch main --limit 10`. What shipped? What's stuck?
- **Session signal** via cc-explorer over `~/projects/jobsearch-buddy`. Starter patterns: `"^no\b"`, `"don't"`, `"NORTH_STAR"`, `"tool.*description"`, `"search.*not working"`, `"couldn't find"`, `"^perfect"`, `"^bombs away"`, `"frustrating"`. Extend per run.
- **Workspace state.** What's in `claude-workspace/observations/`, what's stale, what no longer matches code.

Tool failures: keep going. File a `dream-question` issue with diagnosis. Don't retry; don't block the rest of the run.

### Phase 2 — Think

Synthesize. What is jsb's actual state?

**Patterns to look for:**

- **Silent scraper breakage** — a store's sync_run success rate dropped between runs. Cluster by error shape (selector miss? auth challenge? rate limit? schema change?). The store doesn't know it's broken; Cory won't know unless this run catches it.
- **Data quality drift** — `short_jd` length outliers, distill failures, company bio staleness (bios older than 90 days for recently active companies). Quality degrades silently.
- **NORTH_STAR drift** — session patterns where the LLM was searching for a parameter or behavior that would have helped the LLM but not the human. Flag these; they're design smells.
- **MCP tool UX patterns** — searches that required multiple re-tries, tool calls that returned empty results for seemingly valid queries, LLM confusion about parameter shapes. These are tool-description or query-logic bugs.
- **ATS platform opportunities** — job boards Cory has mentioned but jsb doesn't cover yet. Or ATS platforms that recently changed their markup (scraper would silently return fewer results).
- **Tech debt with N≥2 occurrences** — recurring failure shapes, FIXME comments in recently touched files, slow queries.

### Phase 3 — Produce

Outputs serve the job search. Shape is mine.

Possible shapes:

- **State-of-jsb briefing** at `claude-workspace/observations/state-of-jsb.md` — sync health, data quality, what to look at first.
- **`smell` issues** for tech debt ready for triage (GitHub issues, `smell` label).
- **`dream-question` issues** for judgment calls needing Cory's attention.
- **PRs** for small, concrete fixes (scraper selector update, SQL query fix, tool description improvement) — branch `dream/<YYYY-MM-DD>-<slug>`.
- **Workspace notes** (observations, calibration) — direct push.

**PR bias:** if something could be a PR, it should be. An issue describes wanting to fix; a PR shows the fix.

**Honest skip is a feature** — *"sync healthy, no patterns this run"* beats invented work.

### Phase 4 — Critic

If any file outside `claude-workspace/` was changed, review: is the change grounded in observed data (sync logs, session patterns, actual error messages)? Or is it a generalization from training data? Reject the latter. Revert or scope down to what's actually observed.

### Phase 5 — Commit (or no-op)

If anything was promoted: conventional-commit, body explains why. Push to `main`.

If nothing was promoted: no commit. Silence is a valid run shape.

## DB connection

Check `.env` (python-dotenv) or the `DATABASE_URL` env var. The devbox postgres is also reachable via `psql "service=mozicode-devbox"` for the production copy (read-only queries fine; DML needs dry-run first on devbox, then protocol per Cory's data-ops rules).

## Key tables to watch

- `sync_runs` — per-store sync history with status, error messages, counts
- `job_postings` — stale postings (last_seen_at), short_jd null rate
- `companies` — bio staleness (researched_at), missing bios
- `distill_log` (if it exists) — distill failure rate by store
