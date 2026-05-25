# State of jsb — 2026-05-25

Rewritten by each dream run, not appended. Run 11.

## TL;DR

- **Corpus: stable.** 99,339 active jobs, 0 distill backlog, 5,915 enrich backlog (Tesla orphans — PR #69 pending merge).
- **PR queue: 5 open, 0 merges in 11 days.** No new PRs this run. Pending PR gate holds.
- **First distill quality audit: healthy.** Median short_jd 521 chars, range 76–1,253. No systematic truncation; short outliers are international/brief postings.
- **Bio coverage: 100%** across 595 companies. Bios are 19–20 days old (May 5–6 research run), within the 90-day threshold.
- **Qualcomm 403** remains the biggest untracked degradation — 1,868 active jobs stale since Feb 2026. Confirmed domain-level bot detection (same eightfold_v2 fetcher works for Netflix).

## Sync health

**29 companies erroring** on last sync (2026-05-24). No new regressions vs. run 10.

| Class | Companies | Coverage |
|---|---|---|
| Covered by open PRs | testcorp, retool (PR #71); wandb, fly, groq, replicate, adept-ai, wellsaid-labs (PR #72); mistral→Lever, thumbtack→Ashby (PR #70); Tesla orphans (PR #69) | Merge resolves |
| 0-job dead configs, no PR yet | cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup | ~9 companies; deferred until PR queue clears |
| Active-jobs erroring | coinbase (101 jobs), runway (35), wordware (6), synchron (3), continua (2) | Likely moved off their ATS — dead corpus entries |
| Persistent 403 | qualcomm (1,868 jobs, eightfold_v2), mx (4 jobs, workday) | No easy fix path |
| JSON parse / bot detection | netapp (eightfold_v2, 0 jobs) | HTML response, likely bot detection |

Google 502 visible in last sync data; historically self-healing.

## Corpus

- **Active jobs:** 99,339
- **Distill backlog:** 0 (healthy)
- **Enrich backlog:** 5,915 — entirely Tesla orphans (ats IS NULL). Cleared by PR #69 merge.
- **Bio coverage:** 100% (595/595 companies with active jobs have short_bio, long_bio, embedding)
- **Bio freshness:** all researched 2026-05-05 to 2026-05-06; 19–20 days old, within 90-day threshold

## Distill quality (first audit, run 11)

Short_jd length distribution across 93,424 distilled active jobs:

| p10 | p25 | p50 | p75 | p90 | p99 | min | max | avg |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 374 | 444 | 521 | 599 | 672 | 826 |  76 | 1,253 | 523 |

No systematic truncation found. 1,453 Apple JDs under 300 chars — all international postings (Japanese, Korean, Chinese) or genuinely brief roles. Paylocity (226) and Coupang (188) also contribute short-JD counts for the same reason. Not a distill failure.

## PR queue

| PR | Title | Age | Status |
|---|---|---|---|
| #72 | Remove 6 dead company configs | 2 days | Open |
| #71 | Remove testcorp + retool | 3 days | Open |
| #70 | Fix Mistral→Lever, Thumbtack→Ashby | 4 days | Open |
| #69 | Remove Tesla orphan jobs | 6 days | Open |
| #68 | dream: cc-explorer mandatory | 6 days | Open |

No merges since 2026-05-14 (11 days). PR gate holds — no new PRs opened this run.

## What to look at first

1. **Merge PR #70** — direct corpus benefit: 213 stale Mistral/Thumbtack jobs refreshed on next sync.
2. **Merge PR #69** — clears 5,915 enrich backlog (Tesla orphans), frees enrich workers.
3. **PRs #71/#72** — noise reduction: removes ~8 dead configs from sync error log.
4. **PR #68** — protocol fix: cc-explorer session signal moves to mandatory first signal.

## Qualcomm

1,868 active jobs last fetched Feb 2026. Three months stale. eightfold_v2 returns 403 on `careers.qualcomm.com`. Netflix uses the same fetcher and works — this is Qualcomm-specific domain bot detection, not a fetcher bug. No fix without headless fetch (camoufox). If Qualcomm is in active search scope, this is a meaningful gap.
