# Dream candidate queue

Open candidates the dream routine has surfaced. A candidate is something
that *could* be a PR, an issue, or a workspace investigation in a future
run, but isn't acted on this run because (a) it needs operator judgment,
(b) it's lower-leverage than the run's primary target, or (c) we lack
ground truth to act confidently.

**Promotion rule (from dream.md):** open candidates ≥3 runs old without
engagement are eligible-for-primary-target the next run.

Format: `seeded-on / shape / one-line description / state`.

---

- **2026-05-15 / pattern / Stale rows when a sync errors before listing jobs.**
  The ats=NULL orphan path is resolved by PR #69. The broader pattern —
  a company's fetcher errors before listing and existing jobs accumulate stale —
  still has no detection mechanism. But without `sync_status` history (see
  last-attempt-wins candidate below), this can't be detected from DB alone.
  Superceded by PR #69 for the active instance. Residual concern is the
  general detection problem. Open. Runs seen: 3.

- **2026-05-16 / pattern / `ats IS NULL` resolves into two distinct shapes.**
  **Shape A RESOLVED: PR #69 filed 2026-05-19.** Migration marks Shape A jobs
  (ats=NULL parent + description=NULL + listing_status=active) as removed.
  616/616 tests pass. Run `jsb migrate` after merge.
  Shape B (bio-only research entries, ats=NULL, zero jobs) unaffected — intentional state.
  Candidate closed.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 3.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table
  (jobs_id, model, input_tokens, output_tokens, cached_input_tokens,
  cost_usd, ran_at). Without it, cost regressions cannot be caught from
  the DB alone.
  Open. Runs seen: 3.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  ~16 companies are configured with an Ashby/Greenhouse/Lever slug that
  doesn't resolve and have never accumulated any jobs. Three resolutions:
  (a) correct the slug (some renames are guessable: `cal` → `cal.com`,
  `flywire2` → presumably `flywire`), (b) mark the company disabled via
  the flag proposed in #42, (c) delete the row. The operator's call.
  Open. Runs seen: 3.

- **2026-05-15 / question / Open dream PRs #65 and #67 without operator engagement.**
  Run-5 escalation clause fired. Root cause identified via cc-explorer:
  #65/#67 target Uber faucet config + Taleo parser — neither is a company
  the operator is currently applying to. Operator is in job-application mode
  (direct session quote). Dream protocol fix: PR #68 moves cc-explorer to
  mandatory first signal so target selection reflects operator's active focus
  before DB queries are even run.
  #65/#67 remain open — not stale, just lower priority than operator's current
  search-quality work. Candidate updated. Runs seen: 5.

- **2026-05-15 / question / Is `claude-workspace/observations/` the right home?**
  The dream routine writes to a committed directory. The operator's
  natural re-read path is unclear — these files don't surface in
  `git status`, don't trigger CI, don't ping anyone. Maybe the briefing
  should also open a GitHub issue as a "look-at-me" lever? Or perhaps a
  short summary in the *commit body* is enough. Worth one run's
  attention.
  Open. Runs seen: 2.
