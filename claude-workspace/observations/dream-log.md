# Dream log

Append-only ledger of dream runs. Newest entries at the top. Phase 0
reads the last 3 entries to check for pattern-lock.

Each entry: date, primary target chosen, output shape, candidates
seeded, what was deferred.

---

## 2026-05-15 — run 1 (bootstrap)

**Primary target:** corpus health snapshot. First run, no candidate
queue to draw from, no prior log to pattern-check against.

**Output shape:** state-of-jsb briefing + candidate-queue seed +
this log. No PRs filed; no GitHub issues opened.

**Headline finding:** one scraper has been silently 403'ing for 9 days
while its 5.9k active job rows accumulate as `description=NULL` noise
in the corpus. Class-of-behavior: when a sync errors before the
listing step, existing job rows are never touched and become stale.

**Secondary finding:** 27/647 companies in error state. Most are
"404 with 0 jobs" config errors that suggest the operator's
`#42 (Add explicit company-disable flag)` is the right shape to
pursue, but the choice of which companies to disable vs. fix-the-slug
is the operator's call.

**Meta-finding:** the dream protocol document references DB structures
that don't exist (`sync_runs` table, "distill telemetry"). Either the
protocol predates a schema change or it was written from a planned
future state. Recorded as candidates so a future run can either
rewrite the protocol or add the missing tables.

**Candidates seeded:** 6 (stale-rows-on-listing-error, append-only sync
history, distill telemetry, 404-with-0-jobs cleanup, open-dream-PR
engagement, observations-home).

**Deferred:** cc-explorer session-signal pass (skipped — no calibration
on what to look for in run 1, expensive to scan); per-distill cost
analysis (no telemetry table); PR reviews of #65 / #67 (open without
comments, not blocking).

**Keep-ability self-rating:** pause. The briefing surfaces something
the operator could not see from inside a session (one company holding
5.9k undead rows) and the candidate queue is concrete rather than
performative. Whether it crosses into *merge/act-on* depends on
whether the operator pursues the stale-rows detection PR — that's the
next run's signal to read.
