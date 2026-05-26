# State of jsb — 2026-05-26

Rewritten by each dream run, not appended. Run 12.

## TL;DR

- **Operator is in active application mode** — last live session May 18-19. Friction: date-field confusion (`posted_since` filters by `effective_date` but results show `published_at`; LLM can't reconcile why an old-looking job appeared), and stale evergreen req noise.
- **Corpus: stable.** 99,174 active jobs, 0 distill backlog, 5,916 enrich backlog (Tesla orphans — PR #69 pending).
- **PR queue: 5 open, 0 merges in 12 days.** No new PRs this run. Gate holds.
- **Issue #63 closed.** Root cause resolved — the `ai-as-product` watchlist has a `location_filter` already set in the DB. Filed on May 13, fixed without a code change.
- **Qualcomm 403** remains the largest single degradation — 1,868 jobs stale since Feb 2026.

## Session signal (run 12 — cc-explorer worked)

Operator had two live sessions May 18-19 (7-8 days ago). Both involved real search friction:

**Date field confusion:** LLM callers don't reliably distinguish `posted` (published_at) from effective recency (effective_date = COALESCE(last_listing_update, published_at)). When a job ranked high because of a recent `last_listing_update` but showed an old `published_at` in results, the operator noticed the LLM misread the signals. Root: `search_jobs` returns `"posted"` (published_at) but `posted_since` filters by `effective_date`. 33,063/99,174 active jobs (33%) have `last_listing_update` — for those, the gap is real. Fix: expose `updated` (last_listing_update) in search results alongside `posted`.

**Staleness noise:** Operator frustrated by evergreen/perennial listings mixing with fresh ones. Freshness-bucket ranking is in place but isn't eliminating the noise for the operator's watchlists.

**Watchlist composition:** On May 18, an LLM rewrote a watchlist's built-in filter instead of composing on top of it. This is an LLM behavior gap, not a code bug — but may benefit from a clearer tool description on how watchlist filters compose.

**Urgency signal:** "Just go man, I need to use the tool to apply for jobs" (repeated 3x on May 18). Feature velocity is fine; review bandwidth is the constraint.

## Sync health

**29 companies erroring** on last sync (2026-05-25). No new regressions vs. run 11.

| Class | Companies | Coverage |
|---|---|---|
| Covered by open PRs | testcorp, retool (PR #71); wandb, fly, groq, replicate, adept-ai, wellsaid-labs (PR #72); mistral→Lever, thumbtack→Ashby (PR #70); Tesla orphans (PR #69) | Merge resolves |
| 0-job dead configs, no PR yet | cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup | ~9 companies; deferring until PR queue clears |
| Active-jobs erroring | coinbase (101 jobs), runway (35), wordware (6), synchron (3), continua (2) | Likely moved off their ATS — dead corpus entries |
| Persistent 403 | qualcomm (1,868 jobs, eightfold_v2), mx (4 jobs, workday) | No easy fix path |
| Intermittent timeout | harvey (259 jobs, ashby) | Last_seen 2026-05-24; board responds 200 in <1s; pattern is intermittent not structural |
| JSON parse / bot detection | netapp (eightfold_v2, 0 jobs) | HTML response |

## Corpus

- **Active jobs:** 99,174
- **Distill backlog:** 0 (healthy)
- **Enrich backlog:** 5,916 — entirely Tesla orphans (ats IS NULL). Cleared by PR #69 merge.
- **last_listing_update coverage:** 33,063/99,174 active jobs (33%) — relevant to the date-field confusion issue above
- **Bio coverage:** 100% (595/595 companies with active jobs)
- **Bio freshness:** researched 2026-05-05 to 2026-05-06; 20-21 days old, within 90-day threshold

## Distill quality (audited run 11 — still current)

Short_jd length distribution across 93,424 distilled active jobs:

| p10 | p25 | p50 | p75 | p90 | p99 | min | max | avg |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 374 | 444 | 521 | 599 | 672 | 826 |  76 | 1,253 | 523 |

No systematic truncation. Short outliers are international postings (Apple Japan/Korea) or genuinely brief JDs — not a distill failure.

## PR queue

| PR | Title | Age | Status |
|---|---|---|---|
| #72 | Remove 6 dead company configs | 3 days | Open |
| #71 | Remove testcorp + retool | 4 days | Open |
| #70 | Fix Mistral→Lever, Thumbtack→Ashby | 5 days | Open |
| #69 | Remove Tesla orphan jobs | 7 days | Open |
| #68 | dream: cc-explorer mandatory | 7 days | Open |

No merges since 2026-05-14 (12 days). Gate holds.

## What to look at first

1. **Merge PR #70** — direct corpus benefit: 213 stale Mistral/Thumbtack jobs refreshed.
2. **Merge PR #69** — clears 5,916 enrich backlog.
3. **PRs #71/#72** — removes ~8 dead configs from sync error log.
4. **Date field confusion** — `search_jobs` should return `updated` (last_listing_update) alongside `posted` (published_at) so LLMs can reason about why a job appeared despite an old posted date. Small code change, no schema work needed.

## Qualcomm

1,868 active jobs last fetched Feb 2026. eightfold_v2 returns 403. Netflix uses same fetcher and works — Qualcomm-specific bot detection. No fix without headless fetch (camoufox). Corpus entries are 3+ months stale.
