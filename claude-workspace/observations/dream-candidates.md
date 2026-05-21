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
  still has no detection mechanism. Without `sync_status` history (see
  last-attempt-wins candidate below), this can't be detected from DB alone.
  Superseded by PR #69 for the active instance. Residual concern is the
  general detection problem.
  Open. Runs seen: 5.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 5.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table.
  **Status: blocked on schema.** Needs operator call on whether to add telemetry
  storage. Not actionable without that decision. Stopped counting as a target.
  Open. Runs seen: 5.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  ~16 companies are configured with a dead board slug and have never
  accumulated jobs. Fix options:
  (a) Correct the slug: `flywire2` → `flywire` (clearly wrong),
      `testcorp` test entry → delete.
  (b) Mark disabled via #42 flag.
  (c) Delete the row.
  The operator's call for most; `flywire2` and `testcorp` are clearly
  actionable without judgment.
  Open. Runs seen: 5.

- **2026-05-15 / question / Open dream PRs without operator engagement.**
  5 open dream PRs as of 2026-05-21:
  - #65 (Uber/faucet config) — open since 2026-05-14, 7 days, 0 comments
  - #67 (Taleo fetcher fix) — open since 2026-05-14, 7 days, 0 comments
  - #68 (dream protocol: cc-explorer mandatory) — open since 2026-05-19, 2 days
  - #69 (Tesla orphan cleanup migration) — open since 2026-05-19, 2 days
  - #70 (ATS slug fixes: Mistral → Lever, Thumbtack → Ashby) — open since 2026-05-21
  Zero engagement on all. cc-explorer signal (run 7): operator is in active
  job-application mode, not dev mode — explains low review bandwidth.
  Next calibration: if #65/#67 remain at 0 comments past run 9, consider
  closing them (7→14 days without review likely signals low priority).
  Open. Runs seen: 7.

- **2026-05-15 / question / Is `claude-workspace/observations/` the right home?**
  The dream routine writes to a committed directory. The operator's
  re-read path is unclear. Worth one run's attention.
  Open. Runs seen: 4.

- **2026-05-20 / pattern / Active-jobs-with-404: 5 companies still unresolved.**
  PR #70 fixes Mistral (178 jobs) and Thumbtack (35 jobs). Remaining:
  - Coinbase (greenhouse/coinbase, 101 jobs): API 404 but UI works. May be
    Greenhouse API restriction or new URL format (`job-boards.greenhouse.io`).
  - Runway (greenhouse/runwayml, 35 jobs): moved to Notion (unsupported). Dead config.
  - Wordware (ashby/wordware.ai, 6 jobs): API 404 but UI works. Ashby API restriction?
  - Synchron (greenhouse/synchron, 3 jobs): API 404, still on Greenhouse per web search.
    Slug capitalization or API restriction.
  - Continua AI (ashby/continua, 2 jobs): API 404 but UI works. Ashby API restriction?
  Coinbase is highest-value (101 stale jobs). Operator judgment on which to investigate.
  Open. Runs seen: 2.

- **2026-05-20 / gap / cc-explorer availability.**
  Run 5 used the skill directly (worked). Run 6 failed (MCP tools not loaded).
  Run 7: worked via background subagent approach. Subagent pattern is viable —
  add to protocol. The "cc-explorer is best-effort" note in PR #68 covers this.
  **Partially resolved:** subagent pattern works. No further protocol change needed.
  Open. Runs seen: 2.
