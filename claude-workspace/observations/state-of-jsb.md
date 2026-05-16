# State of jsb — 2026-05-16

Rewritten by each dream run, not appended. Run 2.

## TL;DR

- **Sync health: stable at the same baseline as yesterday.** 28/647 companies in error state (4.3%, vs. 27 yesterday). Net change is within the noise of one daily run — today's deltas are a couple of likely-transient blips and one new persistent-looking 403 against a large-employer careers API.
- **Headline finding (new shape): orphan-job rows from `ats IS NULL` companies.** Two `companies` rows have their `ats` field cleared but still own `listing_status='active'` jobs. One of them holds the ~5.9k undead rows surfaced in yesterday's briefing — those rows now have a sharper cause: the company was un-wired from the sync pipeline (presumably as a manual workaround for a long-running scraper block), and clearing `ats` does not cascade to `jobs`. The rows remain in `search_jobs` results forever, will never re-fetch, will never distill.
- **Corpus body looks healthy.** 93,677 / 99,596 active jobs (94%) have `short_jd`. Distill backlog is 0. Bio coverage is 100%: zero companies are missing `long_bio` or `bio_embedding`, and zero bios are older than 90 days.
- **The two open dream PRs (#65, #67) are still open with no comments since 2026-05-14.** Yesterday's briefing flagged them; today is the second run with no engagement. This briefing is intentionally not stacking a third dream PR on top.

What to look at first: the dream PR pair. If they're not the right shape, that's a signal worth giving back to the routine. If they are, merging them shrinks today's review surface before it grows.

---

## Sync health (class-of-behavior)

`sync_status` shape is unchanged: most-recent-attempt-wins, no history table. Today's 28 errors break down:

| Shape | Count vs. yesterday | Diagnosis |
|---|---|---|
| Ashby 404 with 0 jobs in corpus | ~11 (same) | Board slug never resolved or company removed their public board. Config error. |
| Greenhouse 404 with 0 jobs | ~6 (same) | Same config-error shape. |
| Greenhouse 404 with jobs already in corpus | ~3 (same) | Board moved/renamed; orphans accumulating slowly. |
| Workday-style careers-API 403 with jobs in corpus | ~3 (was ~3; one new) | Endpoint refusing requests — fingerprint, rate limit, or permanent block. **One new entry today** against a large-employer careers API holding ~1.9k jobs. Could be transient; worth re-checking next run before treating as a regression. |
| Lever 404 | 1 (same) | Board moved/renamed. |
| Transient timeout / 502 | 2 (was 0) | One read timeout, one 502 against a high-volume careers app. Both look like transients tied to a single sync attempt; expect recovery on the next run. |
| Ashby/Greenhouse with very stale `last_sync` (>30 days) | 2 | These are unregistered (`ats IS NULL`) — see headline below. The `sync_status` row is frozen because the daily sync no longer attempts them. |

The big stable take: there's no scraper regression visible at the sync-health layer between yesterday and today. The "new" entries today are all consistent with normal daily noise plus one watch-this 403.

## Headline: `ats IS NULL` orphan jobs

Two `companies` rows currently have `ats=NULL`. One owns 5,911 active job rows whose descriptions are all NULL. The other owns zero.

Sequence of events that produces this state:
1. A company was registered with a working scraper.
2. The scraper got blocked at the listing-endpoint layer (a 403 that persisted past the daily-sync threshold).
3. To stop the sync from burning HTTP calls on a known-broken fetcher, `companies.ats` was set to NULL.
4. The `jobs` rows for that company kept their `listing_status='active'` value. Nothing in the codebase archives jobs when their parent company's `ats` is cleared.

The result: undead rows surface in `search_jobs` results, score against FTS like normal postings, and have no `description` to match against — so they degrade snippet quality and inflate the active-jobs headline.

**Class-of-behavior fix shapes** (no operator action implied yet — the right one is the operator's call):

- **Migration-only cleanup.** A single SQL update that marks all `jobs` where `company_slug IN (SELECT slug FROM companies WHERE ats IS NULL)` as `listing_status='removed'`. One-shot; doesn't prevent recurrence.
- **Cascade-on-unregister.** Wrap `ats`-clearing in a function that also marks the company's jobs `removed`. Prevents recurrence but assumes there's a single code path that clears `ats` (today there isn't — it's an ad-hoc operator UPDATE).
- **Search-layer filter.** `search_jobs` excludes rows whose company has `ats IS NULL`. Cheaper than data cleanup, but adds a join to a hot path.
- **The proper version of #42 (`Add explicit company-disable flag`).** A `disabled` boolean on `companies` whose semantics include "archive my jobs." Replaces the ad-hoc `ats=NULL` pattern entirely.

The fourth is the load-bearing fix; the first three are bridges to it.

## Corpus health

| Metric | Today | Yesterday |
|---|---|---|
| Active jobs | 99,596 | 99,776 |
| Active jobs with `short_jd` | 93,677 (94%) | ~94% |
| Distill backlog (active + description + no `short_jd`) | 0 | 0 |
| Active jobs with `description IS NULL` | 5,919 | 5,916 |
| → of which from `ats IS NULL` companies | 5,911 | 5,911 |
| Companies | 694 | 647 |
| Companies missing `long_bio` | 0 | 0 |
| Companies missing `bio_embedding` | 0 | 0 |
| Bios older than 90 days | 0 | 0 |

The company count jumped 647→694 day-over-day. Worth a sanity-check on next run that the bio + embedding pipeline kept pace with the new arrivals (today's snapshot says yes — 0 missing — but a 47-company bulk add deserves a follow-up sample).

## Cost

Schema still has no per-distill telemetry. The first-order signals visible from row state alone:

- Distill kept up: backlog is 0, same as yesterday.
- Distill never burns work on the orphan rows: predicate requires `description IS NOT NULL`, which the 5,911 orphans fail.
- New companies got bios and embeddings without leaving a gap.

Cost regression detection still depends on a telemetry table that does not exist (run 1 candidate #3, still open).

## What changed lately

`git log --oneline -20 main` headline: nothing new since yesterday's run-1 commit. CI on `main` is green; no failed deploys.

## Open dream PRs

- **#65** (Uber faucet): open since 2026-05-14, no comments through 2 dream runs.
- **#67** (Taleo correctness fixes): open since 2026-05-14, no comments through 2 dream runs.

Neither blocks `main` or each other. The decision this run is to *not* stack a third PR — the bottleneck is review, not code production.

## Open-question shapes pending operator judgment

Captured in `dream-candidates.md`:
- "404 with 0 jobs" — fix slug, disable, or delete the row?
- "403 with jobs already in corpus" — retry policy, retire fetcher, or mark stale?
- Should `sync_status` retain history instead of last-attempt-wins?
- Is `claude-workspace/observations/` the right home for state-of-jsb?
- (New today) Which of the four `ats IS NULL` orphan-fix shapes does the operator want?
