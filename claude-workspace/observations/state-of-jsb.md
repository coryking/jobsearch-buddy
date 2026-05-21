# State of jsb — 2026-05-21

Rewritten by each dream run, not appended. Run 7.

## TL;DR

- **Corpus: stable.** 99,291 active jobs, 0 distill backlog, ~100% bio coverage.
- **Tesla orphan backlog: unchanged.** PR #69 (orphan cleanup migration) still unmerged. 5,920 jobs with `description=NULL` (almost all Tesla) appear in enrich backlog and can pollute search.
- **ATS slug fix PR filed (#70).** Mistral AI (ashby→lever, 178 stale jobs) and Thumbtack (greenhouse→ashby, 35 stale jobs) verified and fixed. Next sync after merge will refresh their listings.
- **5 companies remain 404-ing** with active corpus jobs. See detail below — most need operator judgment.
- **Operator is in active job-application mode** (cc-explorer confirmed, run 7 via subagent). Not in dev mode. Prior session signal: search quality + `get_application_form` were the concerns.
- **5 open PRs (#65, #67, #68, #69, #70) with zero engagement.** Dream output is outpacing review bandwidth.

## Sync health

**Error totals (as of 2026-05-20 sync):**
| Error class | Companies | Jobs (last sync) |
|---|---|---|
| 404 Not Found (ATS migration / slug) | 24 | 360 |
| 403 Forbidden (anti-bot, auth) | 3 | 7,783 |
| 502 Bad Gateway (transient) | 3 | 6,274 |
| Timeout | 1 | 612 |

**403 persistent** (not transient, won't self-heal):
- Tesla (ats=NULL, 5,911 active jobs): 403 since May 6. PR #69 marks these as removed; blocked on merge.
- Qualcomm (eightfold_v2, 1,868 jobs): `403 FORBIDDEN` on eightfold API. Anti-bot.
- MX (workday, 4 jobs): 403 on Workday API. Low impact.

**502 / transient** (expected to self-heal):
- Google (3,877 jobs), Walmart (1,217 jobs), Adobe (1,180 jobs): 502 on 2026-05-20. Likely transient.

**Timeout** (may be transient):
- Airwallex (612 jobs): single timeout. Watch next sync.

## Active-jobs-with-404 detail

Companies whose ATS boards return 404 but still have active corpus jobs:

| Company | ATS | Board | Active Jobs | Status |
|---|---|---|---|---|
| Mistral AI | ashby | mistral | 178 | **Fixed in PR #70** — moved to Lever/mistral |
| Coinbase | greenhouse | coinbase | 101 | API 404; UI works at job-boards.greenhouse.io/coinbase. Possible Greenhouse API restriction. |
| Runway | greenhouse | runwayml | 35 | Moved to Notion (unsupported ATS). No fix path without Notion support. |
| Thumbtack | greenhouse | thumbtack | 35 | **Fixed in PR #70** — moved to Ashby/thumbtack |
| Wordware | ashby | wordware.ai | 6 | API 404; UI works at jobs.ashbyhq.com/wordware.ai. Possible Ashby API restriction. |
| Synchron | greenhouse | synchron | 3 | API 404; web search confirms still on Greenhouse. Possible slug mismatch. |
| Continua AI | ashby | continua | 2 | API 404; UI works at jobs.ashbyhq.com/continua/. Possible Ashby API restriction. |

**Pattern:** Coinbase, Wordware, and Continua AI all have public job board UIs that work but their machine-readable APIs return 404. This may reflect a change in how these ATSes expose the public listing API. Runway is a straightforward dead config (wrong ATS). Synchron needs slug investigation.

## Data quality

- Distill backlog: 0 (clean)
- `description=NULL` active jobs: 5,920 (all Tesla orphans, cleared by PR #69 on merge)
- Bio coverage: 100% (as of run 6; no new companies added since)

## What to look at first

1. **Merge PR #70** — 213 stale corpus jobs (Mistral + Thumbtack) start refreshing on next sync. Low risk: two simple UPDATE statements.
2. **Merge PR #69** — removes 5,920 Tesla dead rows from search results. Zero schema risk; 619 tests pass.
3. **Coinbase 404** — may need a different scraping approach if Greenhouse blocked their API. Or the operator can investigate `job-boards.greenhouse.io` URL format.
4. **5 open PRs total** — if review bandwidth is the bottleneck, the dream routine can shift to fewer/higher-value PRs.
