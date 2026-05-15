# State of jsb — 2026-05-15

First state-of-jsb briefing. Cadence: rewritten by each dream run, not appended.

## TL;DR

- **Sync health: degraded but not broken.** 27/647 companies in error state (4.2%). Most are 404s on Ashby/Greenhouse boards that likely renamed or never had a correct slug — corpus quality issue, not scraper-code issue.
- **One scraper is genuinely broken.** One Workday-adjacent ATS that gates its public endpoint behind a 403 has been failing for 9 days while still holding ~5.9k active job rows in the corpus. Those rows have `description=NULL` and will never re-fetch — they're pure noise in `search_jobs` results.
- **Corpus body otherwise looks clean.** 99,776 active jobs, 94% have `short_jd`, 0 in the distill backlog, 0 bios older than 90 days, every company embedded.
- **Two open dream PRs (#65, #67) sitting without operator engagement.** Both ship faucet wiring + correctness fixes for ATSes added in #64. Not urgent, but worth a look before more dream PRs stack up.

What to look at first: the stale-rows-from-broken-scraper pattern (see below). It's the only finding here that's actively degrading the operator's search results.

---

## Sync health (class-of-behavior)

`sync_status` shows the most-recent attempt per company. 25 of 27 errors are from the daily run; 2 companies have not synced for 9-30 days. The shapes:

| Shape | Count | Diagnosis |
|---|---|---|
| Ashby 404 with `0` active jobs | 11 | Board slug doesn't match the live `/posting-api/job-board/{slug}` endpoint. Either the company removed their public board, or the slug stored at company-registration time was never right. |
| Greenhouse 404 with `0` active jobs | 6 | Same shape — `boards-api.greenhouse.io/v1/boards/{board}` doesn't resolve. |
| Greenhouse 404 with active jobs already in corpus | 3 | The board *used* to work, accumulated jobs, then 404'd. Either renamed or removed. |
| Workday/Eightfold/Tesla 403 | 3 | Endpoint refusing the request. Could be a fingerprint check, a rate-limit, or a permanent block. |
| Lever 404 | 1 | Same shape as the Greenhouse "moved" case. |
| Transient disconnect | 1 | One-off; will likely recover on next run. |
| JSON parse error | 1 | An Eightfold endpoint returned a non-JSON body (probably an HTML error page). |

**Class-of-behavior takeaway:** when a board slug 404s and the company has 0 jobs, it's almost always a configuration error (wrong slug at registration), not a scraper regression. When it 404s and the company has jobs already, the board moved. The two cases need different fixes — slug-correction vs. row-archival.

This is the use case `#42 (Add explicit company-disable flag)` was filed to address. A flag would let known-broken companies stop burning HTTP calls every day without losing their job history.

## The stale-corpus pattern (concrete, fix-worthy)

When a sync errors **before** it has a job list, the existing `jobs` rows for that company are not touched. They stay `listing_status='active'` indefinitely, with whatever description / short_jd they last had. If the error persists, the corpus accumulates undead listings.

Today this is one company holding **5,911 such rows**. All have `description IS NULL` — they pre-date the description-enrich phase and will never get one because the fetcher can't get past the 403. They show up in `search_jobs` results with no body to match against.

A complete fix has two parts:
- **Detection** — after a fetch errors, if the company's last successful fetch is >N days old, mark the existing active rows as stale or removed.
- **Resilience** — for ATSes that 403 a known endpoint, document the regression and either retire the fetcher or add a workaround.

A partial fix (just the detection half) is small enough to be a PR and grounded enough not to need operator-only judgment. Candidate for next run.

## Cost (best-effort, no telemetry storage)

The DB schema has no per-call distill cost tracking — `jobs.short_jd` lands as a string with no metadata about which model / how many tokens / how much it cost. The dream protocol document references "distill telemetry" but no such table exists.

What we *can* see:
- 0 jobs in the distill backlog. The distill phase is keeping up.
- 5,916 active jobs have `description=NULL` — 5,911 are from the one stalled scraper above. Those 5,916 will never enter the distill backlog because the predicate requires `description IS NOT NULL`. Cost-neutral, but inflates the "active jobs" headline.

Cost regression detection is not possible from the current schema. Filed as a candidate.

## What changed lately

`git log --oneline -20 main` headline:
- Last 7 days: Uber + Oracle Taleo Enterprise ATS support (#64), `core.py`/`mcp_server.py` package split (#61), NORTH_STAR doc + session-continuity rules, account-scoped watchlists (#60).
- All CI runs on `main` have been green.

## Open PR queue

- **#65** (Uber faucet) — open since 2026-05-14, no comments. Migration `021_uber_company.sql` is the only DB-touching part; everything else is fetcher + tests.
- **#67** (Taleo correctness fixes) — open since 2026-05-14, no comments. Pure fetcher + tests; no migration. Blocks #66 (Taleo detail-page is JS-rendered) being closed.

Both are from previous dream runs. Neither blocks the other or anything on `main`.

## Open-question shapes pending operator judgment

Captured in `dream-candidates.md`:
- "404 with 0 jobs" → fix slug, or disable, or delete the row?
- "403 with jobs already in corpus" → retry policy, or retire fetcher, or mark stale?
- Should `sync_status` retain history (append-only) instead of last-attempt-wins?
- Is `claude-workspace/observations/` the right home for state-of-jsb, or should it land somewhere the operator naturally re-reads?
