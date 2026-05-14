# dream.md

A scheduled task fires this file. The trigger is dumb and durable:

> read `claude-workspace/dream.md` and do what it says

**Before anything else, check for `dream-invariants.md` in this directory. If it exists, read it first and obey it.** That file is the operator's; this file is the routine's. Conflicts resolve in favor of invariants.

This file lives in a public repo. Anything written by the routine — commits, PR bodies, issue titles, observation notes that may get committed — is subject to the same rules as the rest of the repo (see `.claude/rules/public-repo.md`). No named-company analysis, no PII, no operator-identifying detail in public artifacts.

## Purpose (not prescription)

The dream serves jobsearch-buddy's core mission: be the best evidence provider so the human searching for a job sees more good matches and less noise. The daily `jsb-sync` already runs scraping and distill. This routine's job is **not** to do what jsb-sync does — it's to watch the system from above: catch silent breakage, surface patterns a single session can't see, and prepare context for the next working session.

**What "useful output" looks like is for the dream to discover.** A landed PR, a sync-health briefing, an MCP tool quality note — different shapes earn their keep in different runs. Don't lock in an answer; evolve.

**Working hypothesis (mutable):** the headline output is a small, grounded PR when one is justified; otherwise a state-of-jsb briefing at `claude-workspace/observations/state-of-jsb.md` leading with sync health, then cost, then data quality, then what the operator should look at first. Try it. If the operator engages, invest; if it goes ignored, change the shape.

**NORTH_STAR constraint:** every output should be checkable against `docs/NORTH_STAR.md`. If a finding or fix optimizes the LLM intermediary at the expense of the human user's outcomes, it's the wrong direction.

## The protocol

Five phases. Order is load-bearing; shape within each phase is the routine's to evolve.

### Phase 1 — Orient

Read what's accumulated since last run, in parallel where calls don't depend on each other:

- **Sync health (primary signal).** Connect to the jsb DB (`JOBBUDDY_PG_SERVICE` env or `.env`; defaults documented in `CLAUDE.md`). Query: most recent 20 sync_runs grouped by status and store. New failure clusters = headline finding. If a store is failing 3+ runs in a row, that's a scraper regression.
- **Cost signal.** Token usage per distill call (input/output/cached) over the last 24h, and the running per-job cost. A regression — e.g. a prompt change that doubled output tokens, or cache-hit rate collapsing — is a headline finding. Pricing lookups live in `eval/models.py`.
- **Project state.** `git log --oneline -20 main`, `gh issue list --state open`, `gh run list --branch main --limit 10`. What shipped? What's stuck?
- **Session signal** via cc-explorer over this project. Starter patterns: `"^no\b"`, `"don't"`, `"NORTH_STAR"`, `"tool.*description"`, `"search.*not working"`, `"couldn't find"`, `"^perfect"`, `"^bombs away"`, `"frustrating"`. Extend per run.
- **Workspace state.** What's in `claude-workspace/observations/`, what's stale, what no longer matches code.

Tool failures: keep going. File a `dream-question` issue with diagnosis. Don't retry; don't block the rest of the run.

### Phase 2 — Think

Synthesize. What is jsb's actual state?

**Patterns to look for:**

- **Silent scraper breakage** — a store's sync_run success rate dropped between runs. Cluster by error shape (selector miss? auth challenge? rate limit? schema change?). The store doesn't know it's broken; nobody will know unless this run catches it.
- **Cost drift** — per-distill token usage trending up, cached-input ratio trending down, or a model/prompt change that quietly raised cost-per-job. Money is the dial nobody watches in real time.
- **Data quality drift** — `short_jd` length outliers, distill failures, company bio staleness (bios older than 90 days for recently active companies). Quality degrades silently.
- **NORTH_STAR drift** — session patterns where the LLM was searching for a parameter or behavior that would have helped the LLM but not the human. Flag these; they're design smells.
- **MCP tool UX patterns** — searches that required multiple re-tries, tool calls that returned empty results for seemingly valid queries, LLM confusion about parameter shapes. These are tool-description or query-logic bugs.
- **ATS platform opportunities** — ATS platforms that recently changed their markup (scraper would silently return fewer results), or platforms surfaced in session logs that jsb doesn't yet cover.
- **Tech debt with N≥2 occurrences** — recurring failure shapes, FIXME comments in recently touched files, slow queries.

### Phase 3 — Produce

Outputs serve the job search. Shape is the routine's.

**Bias toward solutions, not tickets.** An issue describes wanting to fix; a PR shows the fix. If something could be a PR, it should be. Issues exist for questions and judgment calls — not for tech debt that's small enough to fix in this run.

Possible shapes, in preference order:

- **PRs** for concrete fixes (scraper selector update, SQL query fix, tool description improvement, prompt cost regression). Branch `dream/<YYYY-MM-DD>-<slug>`. Small, grounded in observed data, single concern.
- **State-of-jsb briefing** at `claude-workspace/observations/state-of-jsb.md` — sync health, cost, data quality, what to look at first.
- **`dream-question` issues** for judgment calls needing the operator's attention — design tradeoffs, scope decisions, anything where landing a PR without input is the wrong move.
- **`smell` issues** as a last resort, only for tech debt too large to PR in one run.
- **Workspace notes** (observations, calibration) — direct push.

**Honest skip is a feature** — *"sync healthy, no patterns this run"* beats invented work.

### Phase 4 — Critic

If any file outside `claude-workspace/` was changed, review: is the change grounded in observed data (sync logs, session patterns, actual error messages)? Or is it a generalization from training data? Reject the latter. Revert or scope down to what's actually observed.

### Phase 5 — Commit (or no-op)

If anything was promoted: conventional-commit, body explains why. PRs go through normal review; non-PR commits (workspace notes) push to `main`.

If nothing was promoted: no commit. Silence is a valid run shape.

## DB connection

Connection settings live in `src/jobbuddy/settings.py` and are documented in `CLAUDE.md` (`JOBBUDDY_PG_SERVICE`, `JOBBUDDY_POSTGRES_*` env vars). Read-only queries are fine; any DML needs dry-run on a non-prod target first.

## Key tables to watch

- `sync_runs` — per-store sync history with status, error messages, counts
- `jobs` — stale postings, `short_jd` null rate
- `companies` — bio staleness (`researched_at`), missing bios
- distill telemetry — token usage per call, cached-input ratio, cost-per-job
