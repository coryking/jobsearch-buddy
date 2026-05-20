# Phase 0 — 2026-05-20 (run 6)

## Self-audit

**1. Is my plan pointed at the operator's job search going better — or only at "producing output"?**

Per PR #68 (mandate from run 5): cc-explorer session signal is the first gate. Until I know what the operator is doing, I can't know whether any candidate is worth picking. Four candidates are ≥3 runs old (sync_status history, distill telemetry, 404-cleanup, stale-rows detection) but none of them matter if the operator is in job-application mode and needs search quality or application tooling improved. Run cc-explorer first. Pick primary target from what the operator is actually touching.

**2. Will this run make next-me sharper, or just leave next-me another work item?**

Run 5 was good: cc-explorer finally ran, found operator focus, produced a concrete search-quality fix (PR #69). PRs #68 and #69 are both open. If the operator merged neither, next-me gets no new ground truth. If one merged, next-me should build from that. The sharpening question is: does this run produce something that unlocks operator engagement, or does it add to the queue?

Risk: the ≥3-runs candidates are all schema-change / judgment-call items. Stacking another schema PR while #69 is unreviewed would repeat the run-3 mistake. Session signal gates whether any of those candidates are worth activating.

**3. What did this run NOT watch for, and why?**

Across 5 runs — consistent blind spots:
- **MCP tool UX patterns**: Issue #63 (watchlist location filter) has been open since run 1 with no dream attention. The `search_jobs` filter bugs are directly operator-facing.
- **Issue backlog vs. recent commits**: Recent main commits (published_since, ATS filter, get_application_form, Greenhouse trim) suggest active iteration on search and apply paths. Dream hasn't mapped what issues those commits addressed or what's still open in the same area.
- **Specific session content**: Run 5 checked session signal for the first time. But the read was high-level ("operator is in job-application mode"). Did it surface any specific tool failures, confusing parameters, or empty results? Those are the tool-description and query-logic bugs dream.md Phase 2 flags.

**Pattern-lock audit (last 3 runs):**

- Run 5: cc-explorer + two PRs (good shape, mandatory escalation fired)
- Run 4: candidate update + log only (borderline trash)
- Run 3: candidate split + log only (no PR, pattern-lock break)

No 3+ same-shape lock this time (run 5 broke the lock). But: PRs #68 and #69 sitting unreviewed is the continuity signal. If the operator hasn't merged either, the engagement question from run 5 is still open.

**Primary target selection (pre-cc-explorer):**

Mandatory: run cc-explorer first. Likely targets after:
- If operator is still in application mode → look at apply-path tooling (get_application_form, application logging). Issues #44, #63 are operator-facing.
- If operator is doing general search → issue #63 (watchlist location filter) is a concrete tool-quality bug.
- If operator has reviewed/merged #68/#69 → distill telemetry or sync_status history becomes eligible.
