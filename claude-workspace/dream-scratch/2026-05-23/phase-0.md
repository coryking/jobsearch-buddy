# Phase 0 — Run 9 self-audit (2026-05-23)

## 1. Is my plan pointed at the job search going better — or only at producing output?

Partial credit at best. The last 3 runs each produced a PR, but there are now 6 open PRs with 0 operator engagement. That means the dream is generating work product that sits unreviewed. If those PRs don't merge, the fixes don't ship, and the job search doesn't improve. Producing another PR this run risks the same outcome. The question "what is the blocking constraint on getting these fixes merged?" is more important than "what's the next thing to fix?"

## 2. Will this run make next-me sharper, or just leave another work item?

Left-as-is, this run would produce PR #N (say, Coinbase slug investigation), leave it unreviewed alongside 6 others, and log it as "deferred-pending-operator-review." That's not sharper. Sharper would be: (a) check what actually merged — reality may be different from the candidate queue — and (b) audit whether the PR shape is the right shape given 0 engagement.

## 3. What did this run NOT watch for? Negative-space audit.

- **Whether any of the 6 open PRs merged.** The candidate queue was written on 2026-05-22. It's now 2026-05-23. PRs #70/#71 are 1-2 days old and could have merged overnight. Never checked.
- **Whether the Qualcomm 403 is actually eightfold-wide or Qualcomm-specific.** Named it precisely in run 8 but never asked: are other eightfold_v2 boards also failing? If it's a platform-wide regression, there are multiple companies affected, not just one.
- **The "observations-home" candidate** (runs-seen=5, seeded 2026-05-15). Never promoted to primary target despite being eligible since run 4. What was it? It's about whether `claude-workspace/observations/` is the right home for dream output. Six runs without addressing it suggests the dream doesn't read its own candidates carefully.
- **Session signal.** PR #68 (cc-explorer mandatory first signal) is still open. The protocol change hasn't merged. Every run since run 5 is running on the pre-PR-68 protocol. This is a meta-failure.

## Pattern-lock audit (last 3 runs)

| Dimension | Run 6 | Run 7 | Run 8 | Same? |
|---|---|---|---|---|
| Output shape | state-of-jsb, no PR | PR + state-of-jsb | PR + state-of-jsb | Mostly locked |
| Deferred | Coinbase, Runway | Coinbase, Runway | Qualcomm, Coinbase | Coinbase deferred 3 runs |
| cc-explorer | Failed | Worked (subagent) | Failed | Hit-or-miss |
| Open PR engagement | Not checked | Not checked | Not checked | Never checked |

**Mandatory primary target this run:** The Coinbase deferral (3 consecutive runs) plus the open-PR-engagement blindspot (never checked) are tied. The more leveraged one is PR status — checking it could reveal that multiple PRs already merged, which changes the candidate queue substantially. Start there.

## Adjusted plan for this run

1. **Check current PR status** — gh pr list, which merged, which still open.
2. **Run cc-explorer** if available — session signal check.
3. **DB health + Coinbase investigation** — attempt a real Coinbase Greenhouse URL investigation.
4. **Eightfold-wide 403 check** — are other eightfold_v2 boards also failing?
5. **Produce**: If PRs merged, update candidate queue and state-of-jsb. If Coinbase has a fixable slug, PR it. If eightfold-wide regression confirmed, file a smell issue or investigate.
