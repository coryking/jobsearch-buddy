# State of jsb — 2026-05-23

Rewritten by each dream run, not appended. Run 9.

## TL;DR

- **Corpus: stable.** 98,999 active jobs, 0 distill backlog, 5,917 enrich backlog (Tesla orphans — PR #69 pending merge).
- **7 open PRs, 0 merged since May 14.** Output is outpacing review bandwidth.
- **PR #72 (new this run):** removes 6 confirmed-dead company configs (0 total jobs each — acquired companies and ATS-moved companies with unsupported new platforms). Reduces sync error noise.
- **Qualcomm 403** remains the biggest untracked degradation — 1,868 active jobs stale since Feb 2026 (3 months). Persistent eightfold_v2 403.
- **Harvey (ashby/harvey)** timed out in last sync but returned 259 jobs on manual check today. Intermittent, not persistent.
- **NetApp (eightfold_v2)** has a JSON parse error ("Expecting value"), not a 403. Zero jobs. Different failure class from Qualcomm.
- **Hebbia**: old-style `boards.greenhouse.io/hebbia` board shows jobs in web search, but `boards-api.greenhouse.io/v1/boards/hebbia/jobs` returns 404. Possible API-restriction class on old Greenhouse embed boards.

## Sync health

**Error totals (2026-05-22 sync):**
| Error class | Companies | Active jobs affected |
|---|---|---|
| 404 Not Found (slug dead / API restricted) | 24 | ~370 |
| 403 Forbidden (anti-bot, auth) | 4 | 7,783 |
| Timeout (intermittent) | 1 (harvey) | 248 (not stale — seen May 21) |
| JSON parse error | 1 (netapp) | 0 |

**403 persistent** (not transient):
- Tesla (ats=NULL, 5,911 active jobs): 403 since May 6. PR #69 marks these as removed; blocked on merge.
- Qualcomm (eightfold_v2, 1,868 jobs): `403 FORBIDDEN` since Feb 2026. Jobs 3 months stale. No fix path identified.
- MX (workday, 4 jobs): 403. Low impact.
- NetApp (eightfold_v2, 0 jobs): JSON parse error. Bot detection returning HTML? Different failure from Qualcomm 403.

## Active-jobs-with-404 detail

| Company | ATS | Board | Active Jobs | Status |
|---|---|---|---|---|
| Mistral AI | ashby→lever | mistral | 178 | **Fixed in PR #70** — awaiting merge |
| Coinbase | greenhouse | coinbase | 101 | API 404; UI may work via different URL. Needs investigation. |
| Thumbtack | greenhouse→ashby | thumbtack | 35 | **Fixed in PR #70** — awaiting merge |
| Runway | greenhouse | runwayml | 35 | Moved to Notion (unsupported). Dead config — needs disable/delete. |
| Wordware | ashby | wordware.ai | 6 | API 404; UI works. Possible Ashby API restriction. |
| Synchron | greenhouse | synchron | 3 | API 404; still on Greenhouse per web search. |
| Continua AI | ashby | continua | 2 | API 404; UI works. Possible Ashby API restriction. |

## Zero-job dead configs (sync noise, no corpus impact)

About 16 companies error on every sync but have 0 jobs. PR #72 (new) removes 6 of these.
Remaining (~10): testcorp, retool (PR #71 pending), and others with unclear status (cal, turso, monday, tinybird, etc. — all return 403/404, need investigation to categorize as dead vs. fixable slug).

## Data quality

- Distill backlog: 0 (clean)
- Enrich backlog: 5,917 (Tesla orphans — PR #69 clears on merge)
- Bio coverage: 100%
- Harvey: 248 active jobs, last_seen May 21 — not degraded despite timeout in last sync

## What to look at first

1. **Merge PRs #70, #69, #71, #72** — each is a concrete fix. Combined effect: ~213 stale jobs refreshed, 5,917 dead search results removed, ~8 dead sync errors eliminated. All low-risk.
2. **PRs #65 / #67** — 9 days open, 0 comments. At run 9. Consider closing; the PR backlog is becoming its own noise signal.
3. **Qualcomm** — 1,868 jobs stale since Feb. If Qualcomm roles are in scope, search quality is meaningfully degraded. No code fix available without headless fetch.
4. **Coinbase** — 101 active jobs, 404 on v1 API but UI works at `boards.greenhouse.io/coinbase`. Could be an API-restricted board needing a different fetch path.
