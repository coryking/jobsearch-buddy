# State of jsb — 2026-05-24

Rewritten by each dream run, not appended. Run 10.

## TL;DR

- **Corpus: stable.** 99,417 active jobs, 0 distill backlog, 5,915 enrich backlog (Tesla orphans — PR #69 pending merge).
- **PR queue reduced to 5.** PRs #65 and #67 closed today (10-day threshold, 0 engagement). 5 open PRs remain (#68, #69, #70, #71, #72). No merges since May 14.
- **Greenhouse embed-board hypothesis (run 9) not confirmed.** `job-boards.greenhouse.io/coinbase` and `/synchron` both 404 — companies likely left Greenhouse entirely, not using a different board type. Updating candidate.
- **Qualcomm 403** remains the biggest untracked degradation — 1,868 active jobs stale since Feb 2026. Persistent eightfold_v2 403, no fix path without headless fetch.

## Sync health

**28 companies erroring** on last sync (2026-05-23). Breakdown by PR coverage:

| Class | Companies | Coverage |
|---|---|---|
| Covered by open PRs | testcorp, retool (PR #71); wandb, fly, groq, replicate, adept-ai, wellsaid-labs (PR #72); mistral→Lever, thumbtack→Ashby (PR #70); Tesla (PR #69) | Merge resolves |
| 0-job dead configs, no PR yet | cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup (new) | ~9 companies; PR backlog saturated, deferring |
| Active-jobs erroring | coinbase (101 jobs), runway (35), wordware (6), synchron (3), continua (2) | Likely moved off their ATS |
| Persistent 403 | qualcomm (1,868 jobs, eightfold_v2), mx (4 jobs, workday), netapp (0 jobs, eightfold_v2) | No easy fix path |

`evenup` (ashby, 0 jobs) is a new 404 this run, not yet in candidates. Adding.

## Corpus

- **Active jobs:** 99,417
- **Distill backlog:** 0 (healthy)
- **Enrich backlog:** 5,915 — entirely Tesla orphans (`ats IS NULL`). Cleared by PR #69 merge.
- **Bio coverage:** 100% (stable)

## PR queue

| PR | Title | Age | Status |
|---|---|---|---|
| #72 | Remove 6 dead company configs | 1 day | Open |
| #71 | Remove testcorp + retool | 2 days | Open |
| #70 | Fix Mistral→Lever, Thumbtack→Ashby | 3 days | Open |
| #69 | Remove Tesla orphan jobs | 5 days | Open |
| #68 | dream: cc-explorer mandatory | 5 days | Open |

Five open PRs. #68 and #69 are highest-leverage (protocol fix + search quality). #70 unblocks 213 stale corpus jobs on next sync.

## What to look at first

1. **Merge PR #70** — direct corpus benefit: 213 stale jobs refreshed on next sync.
2. **Merge PR #69** — clears 5,915 enrich backlog (Tesla orphans) + frees enrich workers.
3. **PRs #71/#72** — noise reduction: removes ~8 dead configs from sync error log.
4. **PR #68** — protocol fix: ensures future dream runs use session signal before DB queries.

## Qualcomm

1,868 active jobs last fetched Feb 2026. Three months stale. eightfold_v2 returns 403. No fix without headless fetch (camoufox). If Qualcomm is in active search scope, this is a meaningful gap.
