# Phase 0 — Self-audit (Run 10, 2026-05-24)

## 1. Is this run pointed at the operator's job search going better, or only at producing output?

Honest check: runs 7/8/9 each produced a dead-config removal PR. That's legitimate noise reduction (fewer sync errors = healthier corpus), but the signal-to-output ratio is shrinking. Run 9 generated PR #72 removing 6 companies that were already dead — the finding was real, but the operator hasn't merged any of the 7 open PRs in 10 days. Producing PR #73 in the same vein today would be output-for-output's-sake.

The run is pointed right only if it either:
(a) Closes the stale-PR backlog (reduces overhead for the operator on review day), or
(b) Finds something that meaningfully improves job search quality for the operator.

## 2. Will this run make next-me sharper, or leave another work item?

The Greenhouse embed-board gap (seeded run 9, 1 run old) is the highest-quality new signal: it names a *systematic* fetch gap (not individual dead configs), and I have 3 confirmed examples. A PR attempting the embed-board fix would make next-me either see the merge or the operator's feedback — both are sharper than another candidate entry.

The cc-explorer pass has been skipped 2 of last 3 runs. Running it today anchors target selection in operator's actual activity rather than DB noise patterns.

## 3. What did this run NOT watch for?

Pattern-lock risk: runs 7/8/9 all ended with "removed N dead company configs." The dead-config class is nearly exhausted (maybe 5-10 remaining per candidates). The **embed-board gap** is different in kind — it's companies with *active boards* that we're failing to fetch, not dead companies. That's a bigger search-quality issue.

## Across last 3 entries — same shortfall?

Yes. **The same deferred target across all 3 runs:** cc-explorer session signal not checked (runs 8 and 9 explicitly skipped it). And the PR backlog: 7 open PRs, 0 merges, growing each run.

## Mandatory primary target this run

**PR backlog + stale PR closure.** Run 10 threshold for #65/#67 is today per the candidates. Close them with explanatory comments. This serves the operator by reducing review-queue noise.

**Secondary target:** Greenhouse embed-board gap investigation. 1 run old, strong technical signal, potentially more impactful than dead-config cleanup (companies with *active jobs* we're missing).

**cc-explorer:** Attempt it. Gate target selection on what session signal shows about operator's current focus.
