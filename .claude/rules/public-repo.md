# Public Repo

This repository is public on GitHub. Anything committed here — code,
migrations, docs, CLAUDE.md files, the `.claude/rules/` directory you're
reading right now — is visible to the world, indexed by search engines, and
durable in git history past any retroactive scrub.

The same property holds for the GitHub *surface* around the repo: issue
titles and bodies, PR titles and bodies, commit messages, branch names, code
review comments, GitHub Actions logs. Retroactive edits are leaky (edit
history, third-party mirrors, archive services).

## What does not belong on any public surface

- **Real person names.** The operator, family members, friends, anyone else.
  Use generic role labels in code, migrations, docs, and GitHub artifacts:
  `tech-profile`, `trades-profile`, `Profile A / Profile B`, "the operator,"
  "the seeker." This applies even when the LLM is paraphrasing a chat message
  back into an issue body — the chat is private; the artifact stays generic.
- **Named-company analysis derived from the database.** jsb's database is
  scraped from real third parties. The operator typically uses it to study
  hiring patterns at companies they're interested in — perennial reqs,
  GTM-vs-research mix, posting-age outliers, named multi-year-old roles. That
  analysis is private work product about real third parties. The public
  artifact describes the **class** of behavior; the named version goes
  somewhere outside this repo (e.g. `~/notes/`) and is shared with the
  operator in chat.

  - Good public framing: "ATSes that expose only `createdAt` cannot
    distinguish evergreen-with-edits from genuinely-untouched."
  - Bad public framing: "$Company has a 1,088-day-old req we can't explain."

- **The operator's specific job-search targets, preferences, or activity.**
  Industries, role levels, comp expectations, application history, ranked
  shortlists. None of it. The seeker's context is the calling LLM's
  responsibility — jsb does not encode it anywhere, and the repo does not
  document it.
- **Secrets in any form.** API keys, OAuth tokens, connection strings, real
  passwords (even rotated ones), Azure principal names, tenant IDs that are
  not already public, Entra app IDs that are not already public. `.env.example`
  ships with placeholder shapes only.
- **PII from job postings.** Recruiter names, recruiter emails, internal
  hiring-manager names occasionally found in JD text. The distill prompt is
  meant to strip these; if they leak into a test fixture or doc, redact.

## What belongs in private memory, not the repo

The Claude Code auto-memory at `~/.claude/projects/.../memory/` is
machine-local and not version-controlled. It is the right home for:

- Behavioral feedback that names the operator or quotes them directly.
- References to private analysis files (e.g. `~/notes/...`).
- cross-project context that names other private repos or personal paths.
- The session UUIDs that anchor a learning to a specific conversation.

Repo docs should not link into auto-memory paths (`~/.claude/...`) or other
personal paths — those paths don't exist for a fresh clone on a different
machine. If a fact lives in memory and is also useful for repo readers,
**inline the public-safe version in the repo doc**; don't pointer to the
private one.

## The decision

When you have a piece of information and need to decide where it goes:

| Information | Home |
|---|---|
| Architecture, conventions, tooling, ATS quirks, schema | Repo (`docs/`, `CLAUDE.md`, `.claude/rules/`) |
| Class-of-behavior findings ("Greenhouse refreshes within ~30d") | Repo, public GitHub issues |
| Operator's targets, preferences, application history | Auto-memory + private notes; never repo |
| Named-company DB analysis | Private notes outside the repo; class-of-behavior version in repo |
| Real names, contact info, PII | Private surfaces only |
| Operator's working-style feedback, quotes | Auto-memory only |

When in doubt: would a recruiter or employee at a named company be
uncomfortable reading this? If yes, redact to the class-of-behavior version
and put the named version in private notes.

## When retroactive scrub is needed

It happens. Edit the artifact, push the edit, mention in the next commit
body that a scrub occurred — but treat scrubs as failure modes, not as a
backup. Git history retains the original; GitHub edit history retains the
original; mirrors and search-engine caches retain the original. The
operating assumption is "once published, durable." The defense is upstream
of publication.
