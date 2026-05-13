# Handoff Docs Are Ephemeral

Session-handoff documents — files written by one session for the next session
to read, then act on, then discard — have one valid shape: they get
**deleted by the session that consumes them**. No archival, no tombstone, no
"keeping it for reference." Git already has the diff. The next automated
session reading a stale handoff doc treats it as authoritative and walks into
a project state that no longer exists.

This rule exists because handoff docs accumulate silently. They look durable
(they're markdown in `docs/`) and they pattern-match to "documentation," but
their audience is a single future session, their content is a snapshot of
mid-flight work, and they go stale the moment the work lands.

## What counts as a handoff doc

- `docs/plans/<date>-<topic>-handoff.md` and similar.
- `<n>-iter/` scratch directories under `docs/` or the repo root.
- `session-N-fixes.md`, `next-session-todo.md`, `pickup-here.md`.
- Long markdown files whose first paragraph is "the previous session was
  working on X and got stuck at Y."

What it is *not*: design docs, brainstorms with durable conclusions,
reference material with an audience beyond the next session.

## The rule

**The session that consumes a handoff doc deletes it in the same commit
that closes out the work it described.** If the handoff describes a fix and
the fix lands, the handoff is part of the fix's commit and gets removed
there. If the handoff describes a multi-PR initiative, the final PR removes
it. If the work was abandoned, the next session deletes the doc with a
one-line commit explaining the abandonment — leaving it in place is worse
than admitting it died.

**Scan for stale handoff docs at session start.** Anything under
`docs/plans/` or `docs/brainstorms/` more than ~30 days old that reads like
a snapshot of in-flight work is a candidate. If the work landed and the doc
is still there, the doc is stale by definition — delete it. Don't leave a
"this is historical" header; git history is the historical record.

**Smell that you're about to violate this rule:** you're writing a markdown
file whose intended reader is "the next Claude Code session" and whose
intended lifespan is "until that session is done." Stop. The right home is
either:

- A GitHub issue if the work crosses sessions and needs a tracker.
- A commit-body note on the latest commit, if the next session will pick up
  from `git log` anyway.
- A `.claude/rules/` rule if the lesson is general and worth keeping.
- Nothing, if the next session can re-derive the state from the code.

## Durable knowledge has specific homes

When you have something to write down, route by who reads it and when:

| Audience | Home |
|---|---|
| Anybody reading the repo, indefinitely | `docs/`, `README.md`, `CLAUDE.md`, `docs/NORTH_STAR.md` |
| Any future Claude Code session in this repo | `.claude/rules/*.md` |
| Cross-session items needing the operator's eye | GitHub issues |
| The operator's working-style preferences | Auto-memory (`~/.claude/projects/.../memory/`) |
| One specific future session, then gone | Don't write it. Use a commit body or an issue comment. |

If you can't name the audience, the doc shouldn't exist.
