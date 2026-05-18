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

- **2026-05-16 / pattern / `ats IS NULL` resolves into two distinct shapes.**
  Run 3 (2026-05-17) found 49 `ats=NULL` rows, up from 2 at run 2. The DB
  shape splits cleanly:
  - **Shape A — orphan corpus.** `ats=NULL` *with* active jobs whose
    parent company is no longer wired to a fetcher. 1 row today; owns the
    ~5,911 description-NULL postings still surfacing in `search_jobs`.
    Class-of-behavior: clearing `companies.ats` does not cascade to
    `jobs`. Fix shapes from run 2 still apply (cleanup SQL, cascade,
    search-layer filter, or #42's `disabled` flag).
  - **Shape B — research-only entries.** `ats=NULL` *without* any jobs,
    bios populated. 48 rows today; all bio-dated 2026-05-05; not present
    in `sync_status`. Almost certainly the result of researching
    companies-of-interest (via `research-companies` or a bulk seed) ahead
    of wiring a fetcher. Likely intentional — these are
    research-ahead-of-scrape entries, not bugs. They cost nothing because
    no scraper runs and they don't pollute `search_jobs` (no jobs to
    return).
  Two-shape implication: fix work scopes to Shape A; Shape B is a feature
  unless the operator wants `find_companies` to flag bio-only entries
  visibly. Open. Runs seen: 2.

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
  Both opened on 2026-05-14, no comments. Run 4 (2026-05-18): still open,
  still zero comments. PR-shape output remains on hold; favor workspace
  direct-push and candidate-queue sharpening until either PR moves
  (merge, close, or comment). **Run-5 escalation clause:** if run 5 also
  produces only candidate-update + log-entry with no briefing and no PR,
  and #65/#67 are still zero-comment, the meta-process is the primary
  target. Candidate fix shapes at that point: pause the routine, change
  trigger cadence, or PR `dream.md` itself to shrink protocol to match
  observed reality. Open. Runs seen: 4.

- **2026-05-15 / question / Is `claude-workspace/observations/` the right home?**
  The dream routine writes to a committed directory. The operator's
  natural re-read path is unclear — these files don't surface in
  `git status`, don't trigger CI, don't ping anyone. Maybe the briefing
  should also open a GitHub issue as a "look-at-me" lever? Or perhaps a
  short summary in the *commit body* is enough. Worth one run's
  attention.
  Open. Runs seen: 2.
