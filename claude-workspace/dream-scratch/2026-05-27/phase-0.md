# Phase 0 — Run 13 self-audit (2026-05-27)

## 1. Is this run pointed at the operator's job search going better — or only at "producing output"?

The candidate with the clearest operator-impact signal is the date field fix (run 12, HIGH priority):
`search_jobs` returns `"posted": published_at` but filters by `COALESCE(last_listing_update, published_at)`.
Confirmed live operator friction May 18 via cc-explorer. Operator urgency: "just go man, I need to use
the tool to apply for jobs" ×3. This is pointed directly at the job search going better — the LLM
can't reason correctly about result freshness without `last_listing_update` in the output.

## 2. Will this run make next-me sharper, or just leave next-me another work item?

If this run ships the date field fix as a PR, next-me is sharper: one confirmed friction point
is resolved, and the PR is live for the operator to merge when ready. If this run again defers
it to "when gate allows," next-me inherits the same backlog with one more "deferred" count.

## 3. What did this run NOT watch for, and why? Pattern-lock audit.

Last 3 runs (10, 11, 12): all had "no new PR, PR gate holds" as the output shape. 5 open PRs,
0 merges since May 14. The gate was self-imposed to avoid overwhelming review bandwidth.

**Pattern-lock identified:** The "PR gate" has run 3 consecutive times as the blocking reason.
The gate was reasonable when all deferred items were medium-priority. It is NOT reasonable when
a confirmed high-priority, small fix with live operator friction is waiting.

**Negative-space audit:** What haven't I watched for?
- Whether the PR gate is protecting the operator or protecting the routine from commitment.
  Verdict: the gate protected nothing — 5 PRs in the queue and no urgency to add a 6th is
  sound logic for medium-priority fixes. But the date field fix is high-priority and small.
  The operator reviewing 6 PRs vs. 5 is not a meaningful burden difference.
- Whether any of the 5 open PRs could be closed (stale? superseded?) to make room.
  Run 10 closed #65/#67. No further closures since. #68, #69, #70, #71, #72 are all
  still substantive. No obvious closure candidates without operator engagement.

## Mandatory primary target this run

**Gate is broken. Execute the date field fix as a PR.**

The "no PR for 3 runs due to gate" is the mandatory primary target per dream.md rule.
The date field fix is: small (one dict addition in store.py, one tool description update),
directly grounded in observed operator friction, and safe to add to the review queue as #73.

The gate logic was: "don't pile on." But the right question is: "will this PR materially help
the operator?" Yes. Ship it.
