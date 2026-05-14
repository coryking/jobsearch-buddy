# dream.md

A scheduled task fires this file. The trigger is dumb and durable:

> read `claude-workspace/dream.md` and do what it says

**Before anything else, check for `dream-invariants.md` in this directory. If it exists, read it first and obey it.** That file is the operator's; this file is the routine's. Conflicts resolve in favor of invariants.

This file lives in a public repo. Anything written by the routine — commits, PR bodies, issue titles, observation notes that may get committed — is subject to the same rules as the rest of the repo (see `.claude/rules/public-repo.md`). No named-company analysis, no PII, no operator-identifying detail in public artifacts.

## Purpose (not prescription)

**Three nested goals, in priority order:**

1. **jobsearch-buddy serves the operator's job search well** — best evidence provider, more good matches, less noise.
2. **The agent becomes a better helper toward that goal.** Better-helper means *the operator's* sessions get sharper.
3. **The dream is the meta-process that improves the helper.** Sync-health monitoring is evidence the meta-process is working — not the headline. If a run produces fixes but the agent didn't get better, the run was a miss.

The daily `jsb-sync` already runs scraping and distill. This routine's job is **not** to do what jsb-sync does — it's to watch the system from above: catch silent breakage, surface patterns a single session can't see, and prepare context for the next working session.

**Keep-ability bar.** Score every output: *trash* (operator closes without thought — performative), *pause* (operator reads, has to decide — real work even if rejected), *merge/act-on* (output the operator wouldn't have spent their own time producing). Aim for pause-or-better. A heartbeat that says "sync healthy" is *pause* if honest, *trash* if manufactured to look productive.

**What "useful output" looks like is for the dream to discover.** A landed PR, a sync-health briefing, an MCP tool quality note — different shapes earn their keep in different runs. Don't lock in an answer; evolve.

**Working hypothesis (mutable):** the headline output is a small, grounded PR when one is justified; otherwise a state-of-jsb briefing at `claude-workspace/observations/state-of-jsb.md` leading with sync health, then cost, then data quality, then what the operator should look at first. Try it. If the operator engages, invest; if it goes ignored, change the shape.

**NORTH_STAR constraint:** every output should be checkable against `docs/NORTH_STAR.md`. If a finding or fix optimizes the LLM intermediary at the expense of the human user's outcomes, it's the wrong direction.

## The protocol

Phase 0 → three read-handles → think → produce → commit. Phase 0 ordering is load-bearing; the rest is the routine's to evolve.

### Phase 0 — self-audit (before anything else)

Read the last 3 entries in `claude-workspace/observations/dream-log.md`. Write `dream-scratch/<YYYY-MM-DD>/phase-0.md`:

> 1. Is my plan for this run pointed at the operator's job search going better — or only at "producing output"?
> 2. Will this run make next-me sharper, or just leave next-me another work item?
> 3. **What did this run NOT watch for, and why?** Pattern-lock is invisible from inside; only the negative-space audit catches it.
>
> Across the last 3 entries — same shortfall? same output shape? same deferred target? If the same shape fired 3+ runs in a row, that pattern is the **mandatory primary target this run**, regardless of what else looks shiny.

### Phase 1 — three read-handles (load in order)

Files don't get read unless something puts them on the prompted path. Three handles:

1. **The candidate queue** — `claude-workspace/observations/dream-candidates.md` (create if absent). Open candidates ≥3 runs old without engagement are eligible-for-primary-target this run. New candidates discovered during a run get *appended* here, not just narrated in dream-log.
2. **Dialogue check** — open dream-filed PRs/issues. `gh pr list --search "dream in:head" --state open`, `gh pr view <n> --comments`. New operator comments since last run are highest-leverage signal.
3. **External signals**, in parallel where calls don't depend on each other:

- **Sync health (primary signal).** Connect to the jsb DB (`JOBBUDDY_PG_SERVICE` env or `.env`; defaults documented in `CLAUDE.md`). Query: most recent 20 sync_runs grouped by status and store. New failure clusters = headline finding. If a store is failing 3+ runs in a row, that's a scraper regression.
- **Cost signal.** Token usage per distill call (input/output/cached) over the last 24h, and the running per-job cost. A regression — e.g. a prompt change that doubled output tokens, or cache-hit rate collapsing — is a headline finding. Pricing lookups live in `eval/models.py`.
- **Project state.** `git log --oneline -20 main`, `gh issue list --state open`, `gh run list --branch main --limit 10`. What shipped? What's stuck?
- **Session signal** via cc-explorer over this project. Starter patterns: `"^no\b"`, `"don't"`, `"NORTH_STAR"`, `"tool.*description"`, `"search.*not working"`, `"couldn't find"`, `"^perfect"`, `"^bombs away"`, `"frustrating"`. Extend per run.
- **Workspace state.** What's in `claude-workspace/observations/`, what's stale, what no longer matches code.

Tool failures: keep going. Surface as a comment on an existing relevant issue or a workspace observation. Don't retry; don't block the rest of the run.

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

- **PRs** for concrete fixes (scraper selector update, SQL query fix, tool description improvement, prompt cost regression). Branch `dream/<YYYY-MM-DD>-<slug>`. Small, grounded in observed data, single concern. **The numbers being editable in the operator's merge IS the value of the PR.** When you don't have ground truth on a value, that's a *reason to PR* with conservative values, not a reason to write a problem-shaped issue.
- **State-of-jsb briefing** at `claude-workspace/observations/state-of-jsb.md` — sync health, cost, data quality, what to look at first.
- **`smell` issues** for tech debt noticed-but-won't-fix-this-run.
- **Workspace direct-push** — candidate-queue update, observations, calibration.

**Kill `dream-question` as a category.** Problem-shaped, not solution-shaped. If something needs the operator's judgment, open a PR (even a stub one proposing a direction) or surface in workspace observations. Re-flagging questions louder across runs is performative.

**Tickets-are-rumors applies to your own prior output.** A dream-log entry naming a target is a rumor about that target. Audit against the filesystem and the DB before acting.

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
