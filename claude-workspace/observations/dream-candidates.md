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
  Open. Runs seen: 9.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 9.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table.
  **Status: blocked on schema.** Needs operator call on whether to add telemetry
  storage. Not actionable without that decision. Stopped counting as a target.
  Open. Runs seen: 9.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  After PRs #71 and #72 merge, ~9 companies will still error with 0 jobs:
  cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup (new run 10).
  PR backlog is saturated; deferring another dead-config PR until current queue
  clears. Next run: if any of #71/#72 merged, consider a batch cleanup PR for these.
  Note: the Greenhouse embed-board hypothesis for hebbia/flywire is not confirmed —
  see revised embed-board candidate below.
  Open. Runs seen: 9.

- **2026-05-15 / question / Open dream PRs without operator engagement.**
  As of 2026-05-25: 5 open PRs (#68, #69, #70, #71, #72). No merges since 2026-05-14
  (11 days). No new PRs opened run 11 — PR gate held. Future runs: avoid new PRs
  unless existing ones start merging.
  Open. Runs seen: 11.

- **2026-05-20 / pattern / Active-jobs-with-404: 5 companies still unresolved.**
  PR #70 fixes Mistral (178 jobs) and Thumbtack (35 jobs). Remaining:
  - Coinbase (greenhouse/coinbase, 101 jobs): 404 on both v1 API and new embed
    domain (job-boards.greenhouse.io/coinbase). Likely moved off Greenhouse.
  - Runway (greenhouse/runwayml, 35 jobs): moved to Notion (unsupported). Dead config.
  - Wordware (ashby/wordware.ai, 6 jobs): API 403 direct / 404 via fetcher.
  - Synchron (greenhouse/synchron, 3 jobs): 404 on v1 API and new embed domain.
    Likely moved off Greenhouse.
  - Continua AI (ashby/continua, 2 jobs): API 403 direct / 404 via fetcher.
  Open. Runs seen: 6.

- **2026-05-20 / gap / cc-explorer availability.**
  Run 5: worked via skill directly. Run 6: MCP tools unavailable. Run 7: worked via
  background subagent. Run 8: skill loaded, MCP tools not discoverable. Run 9: not
  attempted. Run 10: skill loaded, MCP tools not discoverable. Run 11: same.
  The gap is environment-specific; no protocol fix eliminates it. Best-effort.
  Open. Runs seen: 6.

- **2026-05-22 / pattern / Qualcomm 403 — 1,868 active jobs stale since Feb 2026.**
  Qualcomm eightfold_v2 API returns 403 FORBIDDEN. Job corpus last refreshed
  Feb 2026 — 3+ months stale. Persistent, not transient. Run 11 confirmed:
  Netflix uses the same eightfold_v2 fetcher and works — this is Qualcomm-specific
  domain bot detection, not a fetcher bug. No fix without headless fetch (camoufox).
  Options: camoufox session bootstrap, or disable Qualcomm and accept the staleness.
  Open. Runs seen: 4.

- **2026-05-23 / pattern / Greenhouse embed-board hypothesis — REVISED.**
  Run 9 hypothesized that Hebbia, Coinbase, Flywire use embed-only Greenhouse boards
  not exposed via the v1 API. Run 10 tested: all redirect to `job-boards.greenhouse.io`
  and 404/500. Embed-board hypothesis not confirmed — these companies appear to have
  left Greenhouse. Reclassified: coinbase, synchron, hebbia as likely-moved-off-Greenhouse
  (same as Runway→Notion). Will be included in next batch dead-config PR when queue clears.
  Open. Runs seen: 3.

- **2026-05-24 / pattern / evenup (ashby, 0 jobs) — new dead config.**
  EvenUp returns 404 on Ashby (`api.ashbyhq.com/posting-api/job-board/evenup`).
  Also tried `evenuplegal` — also 404. 0 jobs in corpus. Low priority given PR
  backlog saturation. Include in next batch dead-config PR when queue clears.
  Open. Runs seen: 2.
