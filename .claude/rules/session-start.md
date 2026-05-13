# Session Start

A fresh Claude Code session lands cold. Auto-memory loads, the root
`CLAUDE.md` loads, but the question "what is this project trying to be, and
what shape is the work today?" still needs a deliberate orient. This rule
names the cheap reads that earn their place at the top of a session.

## Orient

1. **Read [`docs/NORTH_STAR.md`](../../docs/NORTH_STAR.md).** It's the
   answer to *why* the system is shaped the way it is. Every design
   decision in the repo is downstream of it. If the task at hand involves
   tool descriptions, distill prompts, bio prompts, MCP surface, or
   anything search-shaped, NORTH_STAR is load-bearing.
2. **Skim `git log --oneline -20`.** What landed this week sets context
   for what's mid-flight.
3. **Skim `gh pr list --state open` if the task touches review.** Open PRs
   are the canonical record of in-flight work; handoff docs are not.
4. **Read the operator's request.** Auto-memory captures the operator's
   working-style preferences. Apply them without asking.

`docs/architecture.md` and `src/jobbuddy/CLAUDE.md` are the next layer down —
read them when the task is in their territory, not eagerly.

## Worktree before write

Per the operator's standing convention, code changes go in a worktree on a
feature branch, never directly to `main`. This applies even when the change
feels small. The `Agent isolation: "worktree"` flow puts the worktree at
`.claude/worktrees/<name>/` automatically — use it, don't run
`git worktree add` by hand.

Documentation-only sessions that touch `docs/`, `.claude/rules/`, or
`CLAUDE.md` may commit on `main` if the operator asked for it directly.
Default is still worktree.

## Verify main is current

Before merging a worktree branch or rebasing onto `main`, fetch:

```
git fetch origin main
git log --oneline main..origin/main  # is local main behind?
```

The operator pushes from multiple machines. A local `main` that looks clean
can be one commit behind origin and merging without checking creates
avoidable conflicts.

## The smell that you're under-oriented

You're proposing a default, a code path, or a behavior claim and you can't
point to a file:line that grounds it. Stop. Read the file. The dominant
failure mode in a cold session is pattern-matching from training data into
a codebase that has its own opinions.
