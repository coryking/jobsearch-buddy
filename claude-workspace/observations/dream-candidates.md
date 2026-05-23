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
  Open. Runs seen: 7.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 7.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table.
  **Status: blocked on schema.** Needs operator call on whether to add telemetry
  storage. Not actionable without that decision. Stopped counting as a target.
  Open. Runs seen: 7.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  ~10 companies still error on sync with 0 jobs (after PR #71 removes testcorp +
  retool, and PR #72 removes 6 more). Remaining: cal, turso, monday, tinybird,
  flywire, hebbia, moment, persona, synchron, continua. Some may be fixable
  slugs, some may be dead companies, some may be API-restriction issues. Need
  investigation per company.
  Note: Hebbia specifically shows a board at `boards.greenhouse.io/hebbia` in
  web search but `boards-api.greenhouse.io/v1/boards/hebbia/jobs` returns 404
  — possible Greenhouse embed-board-only companies that don't expose the v1 API.
  Open. Runs seen: 7.

- **2026-05-15 / question / Open dream PRs without operator engagement.**
  7 open dream PRs as of 2026-05-23:
  - #65 (Uber/faucet config) — open since 2026-05-14, 9 days, 0 comments
  - #67 (Taleo fetcher fix) — open since 2026-05-14, 9 days, 0 comments
  - #68 (dream protocol: cc-explorer mandatory) — open since 2026-05-19, 4 days
  - #69 (Tesla orphan cleanup migration) — open since 2026-05-19, 4 days
  - #70 (ATS slug fixes: Mistral → Lever, Thumbtack → Ashby) — open since 2026-05-21
  - #71 (dead config cleanup: testcorp + retool) — open since 2026-05-22
  - #72 (dead config cleanup batch 2: 6 companies) — opened 2026-05-23
  No merges since 2026-05-14 (9 days). Dream output is clearly outpacing review
  bandwidth. Run 9 threshold for #65/#67: consider closing. If still unmerged
  at run 10, close them with a comment explaining the stale rationale.
  Open. Runs seen: 9.

- **2026-05-20 / pattern / Active-jobs-with-404: 5 companies still unresolved.**
  PR #70 fixes Mistral (178 jobs) and Thumbtack (35 jobs). Remaining:
  - Coinbase (greenhouse/coinbase, 101 jobs): API 404 but UI at boards.greenhouse.io/coinbase
    works. Could be an embed-only board — different fetch path needed.
  - Runway (greenhouse/runwayml, 35 jobs): moved to Notion (unsupported). Dead config.
  - Wordware (ashby/wordware.ai, 6 jobs): API 403 direct / 404 via fetcher. UI works.
  - Synchron (greenhouse/synchron, 3 jobs): API 404, still on Greenhouse per web search.
  - Continua AI (ashby/continua, 2 jobs): API 403 direct / 404 via fetcher. UI works.
  Open. Runs seen: 4.

- **2026-05-20 / gap / cc-explorer availability.**
  Run 5: worked via skill directly. Run 6: MCP tools unavailable. Run 7: worked via
  background subagent. Run 8: skill loaded, MCP tools not discoverable. Run 9: not
  attempted (MCP tools not loaded in this context).
  The gap is environment-specific; no protocol fix eliminates it. Best-effort.
  Open. Runs seen: 4.

- **2026-05-22 / pattern / Qualcomm 403 — 1,868 active jobs stale since Feb 2026.**
  Qualcomm eightfold_v2 API returns 403 FORBIDDEN. Job corpus last refreshed
  Feb 2026 — 3 months stale. Persistent, not transient. No fix path identified.
  If Qualcomm is in the operator's search scope this is a meaningful degradation.
  Options: headless fetch (camoufox), different eightfold URL pattern, or
  disable Qualcomm and accept the staleness.
  Open. Runs seen: 2.

- **2026-05-23 / pattern / Greenhouse embed-board vs v1-API split.**
  Some companies use old-style Greenhouse boards (`boards.greenhouse.io/{slug}`)
  that appear active in web search and show jobs at `boards.greenhouse.io/{slug}/jobs/{id}`,
  but `boards-api.greenhouse.io/v1/boards/{slug}/jobs` returns 404. Confirmed for:
  Hebbia, Coinbase, Flywire. Our fetcher uses only the v1 API. These companies have
  real active postings we're not collecting. Could affect more companies we haven't
  audited. Resolution: add an embed-board fetch path, or find the alternate API for
  these boards.
  Open. Runs seen: 1.
