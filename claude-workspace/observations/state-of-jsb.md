# State of jsb — 2026-05-22

Rewritten by each dream run, not appended. Run 8.

## TL;DR

- **Corpus: stable.** 99,340 active jobs, 0 distill backlog, 5,916 enrich backlog (Tesla orphans, unchanged — PR #69 pending merge).
- **Qualcomm 403 is the biggest untracked degradation** — 1,868 active jobs last_seen Feb 2026 (3 months stale). Persistent eightfold_v2 403; anti-bot likely. No fix attempted yet.
- **Dead config cleanup PR filed (#71).** Deletes testcorp (test entry) and retool (product shutdown). Zero corpus impact; removes sync noise.
- **ATS slug fix PR (#70) still unmerged.** Mistral (178 stale) and Thumbtack (35 stale) await merge.
- **5 open PRs (#65, #67, #68, #69, #70) + 1 new (#71) with zero engagement** on older ones.
- **cc-explorer MCP unavailable this run** (same environment gap as run 6). Last operator signal (run 7 via subagent): active job-application mode.

## Sync health

**Error totals (2026-05-21 sync):**
| Error class | Companies | Jobs in corpus |
|---|---|---|
| 404 Not Found (ATS migration / slug) | ~24 | 400+ |
| 403 Forbidden (anti-bot, auth) | 3 | 7,783 |
| JSON parse error | 1 (netapp) | 0 |

**403 persistent** (not transient, won't self-heal):
- Tesla (ats=NULL, 5,911 active jobs): 403 since May 6. PR #69 marks these as removed; blocked on merge.
- Qualcomm (eightfold_v2, 1,868 jobs): `403 FORBIDDEN` since at least Feb 2026. Jobs are 3 months stale. No fix path identified — may require headless fetch or manual investigation.
- MX (workday, 4 jobs): 403. Low corpus impact.

**502 / transient from prior run:** Google, Walmart, Adobe — not visible in today's sync_status errors. Likely self-healed.

## Active-jobs-with-404 detail

| Company | ATS | Board | Active Jobs | Status |
|---|---|---|---|---|
| Mistral AI | ashby→lever | mistral | 178 | **Fixed in PR #70** — awaiting merge |
| Coinbase | greenhouse | coinbase | 101 | API 404; UI may work via different URL. Needs investigation. |
| Thumbtack | greenhouse→ashby | thumbtack | 35 | **Fixed in PR #70** — awaiting merge |
| Runway | greenhouse | runwayml | 35 | Moved to Notion (unsupported ATS). Dead config. |
| latitude-ai | greenhouse | latitude | 43 | Showed 404 in 2026-05-21 sync; API returns 200 today (run 8). Likely transient. |
| Wordware | ashby | wordware.ai | 6 | API 404; UI works. Possible Ashby API restriction. |
| Synchron | greenhouse | synchron | 3 | API 404; still on Greenhouse per web search. Slug or restriction. |
| Continua AI | ashby | continua | 2 | API 404; UI works. Possible Ashby API restriction. |

## Data quality

- Distill backlog: 0 (clean)
- Enrich backlog: 5,916 (Tesla orphans — unchanged; cleared by PR #69 on merge)
- Bio coverage: 100%

## What to look at first

1. **Merge PR #70** — 213 stale corpus jobs (Mistral + Thumbtack) refresh on next sync. Low risk.
2. **Merge PR #69** — removes 5,916 Tesla dead rows from search. Zero schema risk.
3. **Merge PR #71** — removes testcorp + retool from sync noise. Two safe DELETEs.
4. **Qualcomm** — 1,868 jobs haven't refreshed since Feb 2026. If Qualcomm roles are in scope, these results are months stale and degrading search quality. May need headless fetch or a URL pattern change on the eightfold_v2 fetcher.
5. **PRs #65/#67** — 8 days without review. Consider closing if they're not a priority; the PR backlog is becoming its own noise.
