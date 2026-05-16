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
  When a company's fetch fails at the listing step, its existing `jobs`
  rows aren't touched and remain `listing_status='active'` indefinitely.
  At least one company holds ~5.9k undead rows with `description=NULL`.
  Candidate fix: a partial fix that marks active rows as stale when the
  last successful sync is older than N days. Detection-only is small
  enough to PR; archival policy needs the operator's call.
  Run 2 (2026-05-16) sharpened this: the ~5.9k rows belong to a company
  whose `ats` was set to NULL (manual unregister). The error-before-listing
  framing is one path to orphans; the bigger path is `ats IS NULL` with
  jobs intact. See the orphan-jobs candidate below.
  Open. Runs seen: 2.

- **2026-05-16 / pattern / `ats IS NULL` orphans jobs in the corpus.**
  Two companies have `ats=NULL`; one holds 5,911 `listing_status='active'`
  jobs with `description=NULL` that will never re-fetch. Class-of-behavior:
  clearing `companies.ats` does not cascade to `jobs`. Four candidate fix
  shapes (see state-of-jsb.md headline): one-shot SQL cleanup,
  cascade-on-unregister, search-layer filter that excludes `ats IS NULL`
  companies, or the proper `disabled` flag from #42. The fourth is
  load-bearing; the first three are bridges. The operator's call on which
  to pursue. Open. Runs seen: 1.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 2.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table
  (jobs_id, model, input_tokens, output_tokens, cached_input_tokens,
  cost_usd, ran_at). Without it, cost regressions cannot be caught from
  the DB alone.
  Open. Runs seen: 2.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  ~16 companies are configured with an Ashby/Greenhouse/Lever slug that
  doesn't resolve and have never accumulated any jobs. Three resolutions:
  (a) correct the slug (some renames are guessable: `cal` → `cal.com`,
  `flywire2` → presumably `flywire`), (b) mark the company disabled via
  the flag proposed in #42, (c) delete the row. The operator's call.
  Open. Runs seen: 2.

- **2026-05-15 / question / Open dream PRs #65 and #67 without operator engagement.**
  Both opened on 2026-05-14, no comments. Not blocking anything. Next
  dream run should check: are they still open and still un-commented?
  If yes for 3+ runs, the dream routine is producing PRs faster than the
  operator can review — that's a shape problem and worth a state-of-jsb
  callout instead of stacking more PRs.
  Open. Runs seen: 2.

- **2026-05-15 / question / Is `claude-workspace/observations/` the right home?**
  The dream routine writes to a committed directory. The operator's
  natural re-read path is unclear — these files don't surface in
  `git status`, don't trigger CI, don't ping anyone. Maybe the briefing
  should also open a GitHub issue as a "look-at-me" lever? Or perhaps a
  short summary in the *commit body* is enough. Worth one run's
  attention.
  Open. Runs seen: 2.
