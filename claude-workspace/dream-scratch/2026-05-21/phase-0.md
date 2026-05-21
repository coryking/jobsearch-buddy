# Phase 0 — Run 7 Self-Audit (2026-05-21)

## 1. Is my plan pointed at the operator's job search going better?

Primary candidate: **active-jobs-with-404** — 7 companies with stale corpus jobs
that will never re-fetch, actively degrading search quality. Fixing their slugs
(or marking them for the operator to investigate) directly benefits job search
results. Yes, this is operator-outcome-pointed.

Secondary: **sync health / DB state** — honest accounting of what's broken.
Also operator-outcome-pointed if it catches new regressions.

Risk: if I produce another log-and-candidates run without any concrete output, I'm
optimizing for the meta-process, not the operator.

## 2. Will this run make next-me sharper?

Yes if: cc-explorer works and I learn what the operator is actually working on.
Yes if: I ship a PR for at least some of the active-with-404 slugs.
No if: I produce observations only and defer again.

## 3. What did this run NOT watch for (negative-space)?

- **Cost / distill telemetry** — deferred every single run (5 out of 5 logged runs).
  No schema to read from. The candidate is 4 runs old with zero movement. I should
  explicitly mark it as "blocked on schema" and stop counting it as a target.

- **Bio quality for the 47-company batch** — mentioned in run 2, never followed up.
  694 companies with 100% bio coverage sounds good; I've never sampled whether the
  bios are actually useful or placeholder-length.

- **Whether 404-with-0-jobs companies could just be deleted** — flagged run 1,
  candidate still open at runs-seen=4. The `flywire2` and `testcorp` entries were
  called "clearly actionable" in run 1. I've not touched them.

## 4. Cross-3-run pattern check

| | Run 4 | Run 5 | Run 6 |
|---|---|---|---|
| Output shape | observations only | 2 PRs | observations only |
| cc-explorer | deferred | ran (skill) | failed (no MCP) |
| PR filed | no | yes (#68, #69) | no |
| PR engagement | 0 | 0 | 0 |

Pattern: alternating "produce PR" / "observations only." No PR has been merged or
commented on in 7 days. The candidate queue has 6 open items, 4 at runs-seen ≥ 4.

**Mandatory primary target per protocol:** The open-dream-PR-engagement candidate
is at runs-seen=6. That is the dominant persistent signal. But producing MORE PRs
into a zero-engagement queue is the wrong response. The mandatory target is:
**resolve whether the PR output shape is still warranted, or shift shape.**

The cleanest resolution: attempt cc-explorer to understand operator's focus, then
decide. If the operator is actively job-searching (as run 5's signal showed), the
right output is a small, high-signal fix to search quality — not more meta PRs.

**Decision for this run:** Try cc-explorer. If it works, use the signal to select
primary target. If not, focus on: (a) active-with-404 slug fixes as a PR, and
(b) marking the no-schema candidates as explicitly blocked.
