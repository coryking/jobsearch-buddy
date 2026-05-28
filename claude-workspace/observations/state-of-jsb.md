# State of jsb — 2026-05-28

Rewritten by each dream run, not appended. Run 14.

## TL;DR

- **Tesla + Qualcomm: investigation closed.** Both 403 from bot-detection (Akamai / eightfold). Base fetcher already uses Chrome headers — still 403. TLS fingerprinting confirmed. No header fix possible. Issue #74 opened to track camoufox headless integration decision.
- **Corpus: 96,049 active** (down 2,901 from run 13 — normal churn, 11,139 jobs marked removed in last sync). 0 distill backlog. 5,917 enrich backlog (Tesla orphans).
- **6 open PRs, 0 merges in 14 days.** PRs #73 and #70 are highest-value.
- **WorkOS: new 404** (lever/workos, 0 jobs) — add to next dead-config batch.

## Sync health

**29 companies erroring** on last sync (2026-05-27). Up 1 from run 13 (WorkOS is new).

| Class | Companies | Notes |
|---|---|---|
| Covered by open PRs | testcorp, retool (PR #71); wandb, fly, groq, replicate, adept-ai, wellsaid-labs (PR #72); mistral, thumbtack (PR #70); Tesla orphan backlog (PR #69) | Merge resolves |
| 0-job dead configs, no PR yet | cal, turso, monday, tinybird, flywire, hebbia, moment, persona, evenup, **workos** | ~10 companies; batch PR pending PR queue clearance |
| Active-jobs erroring | coinbase (101 jobs), runway (35), wordware (6), synchron (3), continua (2) | Likely moved off their ATS |
| Bot-blocked — large corpus | **tesla (5,911 jobs, Akamai)**, **qualcomm (1,868 jobs, eightfold_v2)** | Issue #74. Camoufox required. No header fix. |
| Small 403 | mx (4 jobs, workday) | Low priority |
| JSON parse / bot detection | netapp (eightfold_v2, 0 jobs) | HTML response |

## Corpus

- **Active jobs:** 96,049 (down 2,901 from run 13; 11,139 jobs removed in last sync — normal churn)
- **Distill backlog:** 0 (healthy)
- **Enrich backlog:** 5,917 — entirely Tesla orphans (description IS NULL). Cleared by PR #69 merge.
- **Bio coverage:** 100% (all companies with active jobs have bio + embedding)
- **Bio freshness:** researched 2026-05-05 to 2026-05-06; ~23 days old, within 90-day threshold

## Bot-blocked scrapers — investigation complete (issue #74)

Both Tesla and Qualcomm return persistent 403s. The base fetcher uses Chrome User-Agent, Accept-Language, DNT, Sec-Fetch-Dest/Mode/Site headers. Still 403 on both. This is TLS fingerprinting + behavioral analysis — not fixable with headers.

**Tesla** (custom `tesla.py`): Akamai Bot Manager. 5,911 active jobs stale since 2026-04-29. The fetcher docstring already names the fix: session cookies or browser-bootstrapped session. Erroring since 2026-05-06.

**Qualcomm** (`eightfold_v2`): 403 FORBIDDEN. Netflix uses same fetcher and works — Qualcomm-specific bot detection. 1,868 active jobs stale since 2026-02-27.

Issue #74 is the tracker for the "invest in camoufox headless" design decision. Candidates updated to point there. No further investigation needed in dream runs.

## Distill quality (audited run 11 — still current)

Short_jd length distribution across 93,424 distilled active jobs:

| p10 | p25 | p50 | p75 | p90 | p99 | min | max | avg |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 374 | 444 | 521 | 599 | 672 | 826 |  76 | 1,253 | 523 |

No systematic truncation. Short outliers are international postings or genuinely brief JDs.

## PR queue

| PR | Title | Age | Priority |
|---|---|---|---|
| #73 | types: document JobRow.updated semantics | 1 day | **HIGH** — direct operator friction fix |
| #70 | Fix Mistral→Lever, Thumbtack→Ashby | 7 days | **HIGH** — 213 stale jobs refreshed |
| #69 | Remove Tesla orphan jobs | 9 days | Medium — clears 5,917 enrich backlog |
| #72 | Remove 6 dead company configs | 5 days | Low — noise reduction |
| #71 | Remove testcorp + retool | 6 days | Low — noise reduction |
| #68 | dream: cc-explorer mandatory | 9 days | Meta — 1 day from close threshold |

No merges since 2026-05-14 (14 days).

## What to look at first

1. **Merge PR #73** — 3-line Pydantic description fix. Directly addresses LLM date-field confusion from your May 18-19 sessions. Trivially small.
2. **Merge PR #70** — Mistral and Thumbtack ATS migrations confirmed. 213 stale jobs will refresh on next sync.
3. **Issue #74** — decide whether to invest in camoufox headless for Tesla (5,911 stale) + Qualcomm (1,868 stale). Options: session bootstrap, full headless, or accept staleness.
4. **Merge PR #69** — clears 5,917 enrich backlog.
5. **PRs #71/#72** — dead-config noise reduction, safe merges.
