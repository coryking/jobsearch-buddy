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
  Open. Runs seen: 4.

- **2026-05-15 / question / `sync_status` is last-attempt-wins, not append-only.**
  We can see *current* error state but not "this scraper has been failing
  for 5 days." The dream protocol document asks for "most recent 20
  sync_runs grouped by status and store" — that table doesn't exist.
  Either rewrite the protocol against the real schema, or introduce a
  small append-only `sync_run_history` table. The latter unlocks
  regression detection (success-rate-dropped-between-runs) which is named
  in dream.md Phase 2 patterns.
  Open. Runs seen: 4.

- **2026-05-15 / question / No per-call distill telemetry stored.**
  The dream protocol references token-usage / cached-input ratio /
  cost-per-job tracking. None of those columns or tables exist. Either
  rewrite the protocol, or add a lightweight `distill_telemetry` table
  (jobs_id, model, input_tokens, output_tokens, cached_input_tokens,
  cost_usd, ran_at). Without it, cost regressions cannot be caught from
  the DB alone.
  Open. Runs seen: 4.

- **2026-05-15 / question / "404 with 0 jobs" config errors — fix or disable?**
  ~16 companies are configured with a dead board slug and have never
  accumulated jobs. Fix options:
  (a) Correct the slug: `flywire2` → `flywire` (clearly wrong),
      `testcorp` test entry → delete.
  (b) Mark disabled via #42 flag.
  (c) Delete the row.
  The operator's call for most; `flywire2` and `testcorp` are clearly
  actionable without judgment.
  Open. Runs seen: 4.

- **2026-05-15 / question / Open dream PRs without operator engagement.**
  PRs #65, #67 (Uber/Taleo) open since 2026-05-14. PRs #68, #69 filed
  2026-05-19, open 1 day. Total: 4 open dream PRs.
  Status: zero engagement on all four. #68/#69 are too new to read; #65/#67
  at 6 days with zero comments confirm these aren't on the operator's
  near-term list. Next escalation: if #68/#69 still have zero engagement
  at run 8 (i.e., after another full week), reassess dream's output shape.
  Open. Runs seen: 6.

- **2026-05-15 / question / Is `claude-workspace/observations/` the right home?**
  The dream routine writes to a committed directory. The operator's
  re-read path is unclear. Worth one run's attention.
  Open. Runs seen: 3.

- **2026-05-20 / pattern / Active-jobs-with-404: 7 companies, scraper regression class.**
  NEW this run. Companies that successfully populated the corpus are now
  returning 404 on their board URLs. Class A (404, not transient):
  Mistral AI (ashby/mistral, 178 jobs), Coinbase (greenhouse/coinbase, 101),
  Thumbtack (greenhouse/thumbtack, 35), Runway (greenhouse/runwayml, 35),
  Wordware (ashby/wordware.ai, 6), Synchron (greenhouse/synchron, 3),
  Continua AI (ashby/continua, 2). Plus 4 Ashby timeouts that may be
  transient (Airwallex 612, Snowflake 417, Skydio 110, Replit 79).
  These companies' jobs will never re-fetch. They need slug correction or
  ATS migration. Operator judgment needed on which to investigate first.
  If Coinbase/Mistral are priority companies, their stale jobs are
  actively degrading search quality.
  Open. Runs seen: 1.

- **2026-05-20 / gap / cc-explorer unavailable in terminal dream environment.**
  The dream protocol mandates cc-explorer as first signal (PR #68). But
  the cc-explorer MCP tools aren't available in the terminal environment
  where the dream routine runs (they require a Claude Desktop MCP server
  connection). Runs 5 and 6 both tried; run 5 got results via the skill
  invocation, run 6 got no MCP tools loaded. This is a tool-availability
  gap: the mandate in PR #68 can't be honored reliably.
  Two paths: (a) document that cc-explorer is best-effort in the protocol,
  or (b) find an alternative session-signal approach (grep JSONL directly,
  which CLAUDE.md discourages). Needs operator input on acceptable fallback.
  Open. Runs seen: 1.
