# State of jsb — 2026-05-19

Rewritten by each dream run, not appended. Run 5.

## TL;DR

- **Corpus: stable and healthy.** 99,499 active jobs, 0 distill backlog, 100% bio coverage.
- **Orphan cleanup: PR #69 filed.** 5,911 Tesla active jobs with `description=NULL` pollute search results. Migration marks them removed. Tests pass (616/616).
- **Dream protocol fix: PR #68 filed.** cc-explorer session signal moved to mandatory first signal in Phase 1. Root cause of 4 runs of misdirected output.
- **Sync errors: same baseline, no new regressions.** ~20 errors, dominated by dead slugs (404/0 jobs), one persistent 403 (Qualcomm), one transient 502 (Google). Pattern unchanged.
- **What to look at first:** PR #69 (orphan cleanup, 1-line migration, 0 risk), then PR #68 (dream protocol doc). #65/#67 can wait — they address fetcher coverage the operator isn't currently targeting.

---

## Corpus health

| Metric | Today | Run 4 |
|---|---|---|
| Active jobs | 99,499 | 99,674 |
| Distill backlog | 0 | 0 |
| Enrich backlog (description IS NULL, active) | 5,920 | ~5,920 |
| → all from ats=NULL company (Tesla) | 5,911 | 5,911 |
| Companies total | 694 | 694 |
| Companies missing bio | 0 | 0 |

The enrich backlog is entirely the Tesla orphan — PR #69 removes it by marking those jobs as `listing_status = 'removed'`. After merge + `jsb migrate`, the enrich backlog drops to 9 (the remaining non-Tesla ats=NULL rows).

## Sync health

~20 companies in error state. Breakdown unchanged from run 4:

| Shape | Count | Note |
|---|---|---|
| Ashby/Greenhouse 404, 0 jobs | ~14 | Dead slugs. Config errors. |
| Greenhouse 404, jobs in corpus | 3 | Board moved; orphans accumulating. |
| Workday 403, jobs in corpus | 2 (mx, qualcomm) | Qualcomm 403 is persistent. mx is intermittent. |
| Google 502 | 1 | Transient. |
| Lever 404 | 1 | Board moved. |

No new regressions since run 4.

## Session signal (cc-explorer) — first run this was collected

Operator is in job-application mode: "just go man, i need to actually, you know, use the tool to apply for jobs and shit." Recent working sessions focused on search quality (freshness ranking, watchlist compose) and application form tooling (`get_application_form`). This explains why PRs #65/#67 (Uber faucet, Taleo parser) are unreviewed — neither targets companies the operator is currently applying to.

This signal would have changed dream's target selection in runs 1–4 if it had been collected then.

## Open dream PRs

| PR | Description | Runs open |
|---|---|---|
| #65 | Uber faucet config | 5+ (stale — not operator's current target) |
| #67 | Taleo listing parser fixes | 5+ (stale — not operator's current target) |
| #68 | dream.md: cc-explorer mandatory (new) | 0 |
| #69 | Migration: remove orphan ats=NULL jobs (new) | 0 |

## Open-question shapes pending operator judgment

- "404 with 0 jobs" dead slugs — fix, disable, or delete?
- `sync_status` history retention vs. last-attempt-wins
- #42 `disabled` flag as the long-term shape for deregistering companies
