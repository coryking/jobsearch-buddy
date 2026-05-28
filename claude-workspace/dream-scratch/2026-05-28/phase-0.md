# Phase 0 — Run 14 (2026-05-28)

## Self-audit questions

**Q1: Is my plan pointed at the operator's job search going better — or just at "producing output"?**

The operator is actively applying for jobs. The biggest concrete harm to that goal:
- Tesla: 5,911 active jobs stale since 2026-04-29 (29 days). These listings may have closed or changed — operator could waste time on dead applications.
- Qualcomm: 1,868 active jobs stale since 2026-02-27 (90 days). Same risk, worse.

Combined: ~8,000 stale jobs (~8% of corpus). This is the real gap. The fix requires camoufox headless integration — a design decision, not a quick code fix.

Secondary harm: 6 PRs open with no merges in 14 days. PR #73 (date field fix, directly addresses May 18-19 friction) and PR #70 (Mistral → Lever refresh, 178 stale jobs cleared) are the highest-value unmerged. The dream can't merge them, but it can be clearer about which ones matter most.

**Q2: Will this run make next-me sharper, or just leave next-me another work item?**

Key resolution to make: Tesla and Qualcomm have been listed as "investigate headers/endpoint" for 6 runs (Qualcomm) and 1 run (Tesla). The investigation is done:
- The base fetcher already uses a Chrome User-Agent and Sec-Fetch headers.
- Both still get 403. This is TLS fingerprinting + behavioral analysis (Akamai for Tesla, bot detection for Qualcomm/eightfold_v2).
- No header tweak will fix this. The only paths are: camoufox session bootstrap, or accept staleness.

If this run produces a single issue proposing "headless integration for bot-blocked scrapers (Tesla, Qualcomm)" and moves both candidates to "resolved to issue," next-me won't keep re-investigating the same already-closed question.

**Q3: What did runs 11/12/13 NOT watch for, and why?**

1. **WorkOS (lever/workos) new 404** — appeared in today's sync data, 0 jobs. Not in any prior run. The dream looks at high-active-job companies; WorkOS at 0 is invisible until it shows up in the error list. Easy to add to the batch dead-config candidate.

2. **Corpus decline** — 98,950 → 96,049 active (-2,901 since run 13). Today's data shows 11,139 jobs marked removed in last 3 days with only 87,906 synced active. This appears to be normal churn (new jobs added, old ones removed). Not a systematic issue, but the dream has never noted directional corpus movement.

3. **PR value hierarchy** — the dream tracks "N open PRs" as a count but doesn't highlight which ones matter most. The operator sees a queue of similar-looking PRs. Runs 11-13 noted the queue without distinguishing priority.

## Pattern-lock check across last 3 runs (11/12/13)

- **Same output shape?** Each run: state-of-jsb rewrite + candidate update + phase-0 + log. PR varies. Not a problem — that's the established shape.
- **Same deferred target?** Tesla appeared in run 13 only (1 run). Qualcomm: 6 consecutive runs deferred. Qualcomm is pattern-locked.
- **Same shortfall?** Runs 11-13 all checked sync health and named the Qualcomm/Tesla degradation without resolving the investigation. The investigation is now complete (confirmed camoufox-required). 

## Primary target this run

Qualcomm (6 runs deferred) and Tesla (1 run, but same problem class) both hit the mandatory-primary threshold by virtue of the completed-but-not-acted-on investigation. Action: open one GitHub issue proposing camoufox headless integration for both, converting them from "keep investigating" to "design decision."

Secondary: add WorkOS to dead-config batch candidate; note corpus decline is normal; update PR priority framing in state-of-jsb.

cc-explorer: launched as background subagent (per the run 12 learning — inline skill is unreliable; background subagent is reliable).
