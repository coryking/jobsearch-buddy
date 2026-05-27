# State of jsb — 2026-05-27

Rewritten by each dream run, not appended. Run 13.

## TL;DR

- **Tesla 403 — largest untracked corpus gap.** 5,911 active jobs (6% of corpus) stale since April 29, sync erroring since May 6. Bigger than Qualcomm (1,868). Was in the error count every run but never specifically called out.
- **PR #73 opened.** `JobRow.updated` now has `Field(description=...)` so the LLM understands `posted` vs `updated` semantics. Fixes confirmed operator friction from May 18-19 sessions.
- **Date field fix correction.** Run 12 identified the fix as "add `updated` to results." The field was already in the code — the actual gap was missing Pydantic description. LLM was seeing `updated: null | string` with no context. PR #73 closes this.
- **6 open PRs, 0 merges in 13 days.** PR gate broken this run by the high-priority date-field fix.
- **Corpus: 98,950 active, 0 distill backlog, 5,917 enrich backlog (Tesla orphans, PR #69 pending).**

## Sync health

**28 companies erroring** on last sync (2026-05-26). Down one from run 12 (likely Harvey recovered).

| Class | Companies | Notes |
|---|---|---|
| Covered by open PRs | testcorp, retool (PR #71); wandb, fly, groq, replicate, adept-ai, wellsaid-labs (PR #72); mistral, thumbtack (PR #70); Tesla orphan backlog (PR #69) | Merge resolves |
| 0-job dead configs, no PR yet | cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup | ~9 companies; deferring until PR queue clears |
| Active-jobs erroring | coinbase (101 jobs), runway (35), wordware (6), synchron (3), continua (2) | Likely moved off their ATS |
| Persistent 403 — large corpus | **tesla (5,911 jobs, custom fetcher)**, qualcomm (1,868 jobs, eightfold_v2) | Tesla stale since April 29; Qualcomm since Feb 2026 |
| Small 403 | mx (4 jobs, workday) | Low priority |
| JSON parse / bot detection | netapp (eightfold_v2, 0 jobs) | HTML response |

## Corpus

- **Active jobs:** 98,950 (down from 99,174 run 12; ~224 listings aged out)
- **Distill backlog:** 0 (healthy)
- **Enrich backlog:** 5,917 — entirely Tesla orphans (description IS NULL). Cleared by PR #69 merge.
- **last_listing_update coverage:** 32,998/98,950 (33%) — relevant to date-field freshness
- **Bio coverage:** 100% (all companies with active jobs have bio + embedding)
- **Bio freshness:** researched 2026-05-05 to 2026-05-06; ~22 days old, within 90-day threshold

## Tesla — new primary degradation finding

5,911 active jobs, all last_seen 2026-04-29. Sync erroring since 2026-05-06 with 403 Forbidden on
`https://www.tesla.com/cua-api/apps/careers/state`. Custom fetcher (`tesla.py`). Tesla is ~6% of
the active corpus — larger than Qualcomm (1,868). Was in the "N erroring companies" count across
all runs but never specifically called out. No fix path identified without network investigation
(header changes? endpoint change? bot detection?).

## Distill quality (audited run 11 — still current)

Short_jd length distribution across 93,424 distilled active jobs:

| p10 | p25 | p50 | p75 | p90 | p99 | min | max | avg |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 374 | 444 | 521 | 599 | 672 | 826 |  76 | 1,253 | 523 |

No systematic truncation. Short outliers are international postings or genuinely brief JDs.

## PR queue

| PR | Title | Age | Status |
|---|---|---|---|
| #73 | types: document JobRow.updated semantics | today | Open |
| #72 | Remove 6 dead company configs | 4 days | Open |
| #71 | Remove testcorp + retool | 5 days | Open |
| #70 | Fix Mistral→Lever, Thumbtack→Ashby | 6 days | Open |
| #69 | Remove Tesla orphan jobs | 8 days | Open |
| #68 | dream: cc-explorer mandatory | 8 days | Open |

No merges since 2026-05-14 (13 days). PR #73 is the high-value quick-merge candidate.

## What to look at first

1. **Merge PR #73** — 3-line fix, directly addresses LLM freshness confusion confirmed from your sessions.
2. **Merge PR #70** — 213 stale Mistral/Thumbtack jobs refreshed.
3. **Merge PR #69** — clears 5,917 enrich backlog.
4. **PRs #71/#72** — removes ~8 dead configs from sync error log.
5. **Tesla 403** — investigate `https://www.tesla.com/cua-api/apps/careers/state` response headers; determine if endpoint changed, bot detection, or auth required. 5,911 jobs at stake.

## Qualcomm

1,868 active jobs last fetched Feb 2026. eightfold_v2 returns 403. Netflix uses same fetcher and
works — Qualcomm-specific bot detection. No fix without headless fetch (camoufox).
