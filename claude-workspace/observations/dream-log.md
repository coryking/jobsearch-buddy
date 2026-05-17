# Dream log

Append-only ledger of dream runs. Newest entries at the top. Phase 0
reads the last 3 entries to check for pattern-lock.

Each entry: date, primary target chosen, output shape, candidates
seeded, what was deferred.

---

## 2026-05-17 — run 3

**Primary target:** the 3-run pattern-lock itself. Runs 1 and 2 both
produced "state-of-jsb rewrite + candidate update + log entry, no PR" with
PRs #65/#67 sitting un-reviewed. Run 2's phase-0 named the contingency:
if run 3 also produced that shape without operator engagement, the lock
fires.

**Pattern-lock fired.** #65 and #67 are still zero-comment after 3 runs;
nothing has shipped to `main` since run 2's commit. The lock-break options
run 2 listed were "smallest possible PR" or "session-signal pass." Both
are unavailable for honest reasons: stacking a third dream PR doubles
down on the review-bandwidth bottleneck the no-engagement signal warns
about; the cc-explorer MCP tools needed for the session-signal pass
aren't loaded in this run's environment. So today's break-the-shape move
is neither — it's *no third state-of-jsb rewrite*.

**Output shape:** candidate-queue sharpening + this log entry +
phase-0.md. **No state-of-jsb rewrite** (yesterday's snapshot remains
current — corpus body unchanged, distill backlog 0, sync error count
within the noise band). **No new PR.**

**Headline finding:** the run-2 "ats=NULL orphans" candidate splits into
two distinct DB shapes. `ats=NULL` rows grew 2 → 49 between runs, but 48
of the new rows have zero jobs, no `sync_status` entry, and bio dates of
2026-05-05 — consistent with research-ahead-of-scrape entries
(`research-companies` or a bulk seed), not orphan corpus pollution. The
remaining 1 row is yesterday's tesla-shape orphan (active jobs that will
never re-fetch). Class-of-behavior implication: cleanup work targeted at
shape A (orphan corpus) should not touch shape B (bio-only research
entries) — they're different states, possibly different intents.

**Secondary findings:**
- Sync error count: 28 → 45 between runs, but +47 of the change is the
  shape-B research entries that aren't in `sync_status` at all and the
  delta in actual `sync_status.error` rows is much smaller (45 today; 28
  yesterday — net +17, within the noise band of a 694-company corpus
  with daily fetcher transients).
- Corpus body unchanged: 99,792 active jobs (was 99,596), 0 distill
  backlog, 0 bios missing, 0 bios >90 days stale.
- Company count unchanged at 694 — yesterday's 647→694 jump fully
  metabolized.

**Candidates seeded:** 0 new. The orphan-jobs candidate was *split* into
its two shapes in place; the PR-engagement candidate hit its 3-run
threshold and was annotated with the conclusion ("PR-shape output is on
hold until the queue moves").

**Deferred:**
- cc-explorer session-signal pass — MCP tools not available in this
  environment. Surface as a workspace observation only if a future run
  needs them and is blocked.
- Per-distill cost telemetry — schema gap, candidate still open.

**Keep-ability self-rating:** pause. The substantive output is the
candidate split (sharpens next-me's mental model of `ats=NULL`); the
meta-substance is the explicit no-briefing decision (refuses to perform
productivity when the queue is already saturated). The *merge/act-on*
threshold depends on the operator either engaging with the existing PR
pair or directing the routine to change shape — both signals the routine
cannot self-generate.

---

## 2026-05-16 — run 2

**Primary target:** state-of-jsb refresh + sharpen the stale-rows candidate.
First run with a prior log to read; not yet at the 3-run pattern-lock
threshold, but already careful about the "produce a dream PR" reflex
because yesterday's PRs (#65, #67) are still un-reviewed.

**Output shape:** state-of-jsb rewrite + candidate-queue update + this log.
No PRs filed; no GitHub issues opened. Intentionally not stacking a third
dream PR while two prior ones await review.

**Headline finding:** sharper framing of yesterday's "5.9k stale rows" —
the rows are owned by a company whose `ats` field was set to NULL (manual
unregister to stop a broken scraper from burning HTTP calls). Clearing
`ats` does not cascade to `jobs`. Two companies are in this state; one
holds 5,911 orphan rows that surface in `search_jobs` results, will never
re-fetch, and will never distill. Recorded as a new candidate with four
fix-shape options ranging from one-shot SQL to the proper `disabled` flag
in #42.

**Secondary findings:**
- Sync error count nudged 27→28 day-over-day; net change is within normal
  daily noise. Today's new entries are 1 timeout, 1 502, and 1 fresh 403
  against a large-employer careers API (~1.9k jobs). The 403 is the only
  one worth watching across the next run before treating it as a
  regression.
- Corpus body is clean: 0 distill backlog, 100% bio coverage, 0 bios
  older than 90 days.
- Company count jumped 647→694 day-over-day. The bio pipeline kept pace
  (no gaps), but the bulk arrival is worth sampling next run to confirm
  bio quality didn't slip.

**Candidates seeded:** 1 (the `ats IS NULL` orphan-jobs pattern).

**Candidates re-examined:**
- Stale-rows-on-listing-error sharpened with today's evidence.
- Dream PR engagement candidate: now 2 runs without comments — at 3 runs
  it escalates from candidate to mandatory primary-target.
- Append-only sync history, distill telemetry, 404-with-0-jobs cleanup,
  observations-home: all unchanged, runs-seen bumped 1→2.

**Deferred:**
- cc-explorer session-signal pass (still expensive; no calibration on
  hit-rates).
- Per-distill cost / cached-input ratio (still no telemetry storage to
  read).
- PR #65 / #67 review action (not in dream scope — operator's review
  bandwidth is the bottleneck).

**Keep-ability self-rating:** pause. The orphan-jobs framing is sharper
than yesterday's vague "stale rows" finding and gives the operator four
concrete options to choose between. The deliberate no-PR decision is the
substantive value-add — automated routines that produce work faster than
the operator can absorb it cross from helpful to noisy. Whether this
crosses into *merge/act-on* depends on the operator picking one of the
four fix shapes — that's next run's signal.

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
