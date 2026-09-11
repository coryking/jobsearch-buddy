-- Registry disposition for boards that no longer answer.
--
-- The MCP surface is stateless live fetch, so `companies.ats`/`board` is the
-- critical path: a row pointing at a dead board hands the human
-- "Live fetch failed for …: 404" with no next step. This migration puts each
-- affected row into the most honest state its data supports.
--
-- Three dispositions, and the reason the first is not DELETE:
--
--   NULL the board  — `core/fetch.py` treats `ats IS NULL` as a self-healing
--     state: `if not company or not company.ats: auto_register_from_url(...)`.
--     Paste any posting URL for that company and the row repairs itself with
--     the live ATS config. A row with a *stale* non-null `ats` never reaches
--     that branch, so it stays broken forever. NULL also keeps the slug,
--     the display name, the researched bios (`save_company` never touches
--     them) and any watchlist membership (`watchlist_members` cascades on
--     company delete), and `list_company_jobs` answers with the actionable
--     "Fetch a specific posting by URL instead — it auto-detects the ATS."
--
--   Repoint      — the board moved and the new one is verified serving.
--
--   DELETE       — reserved for rows that are not a company at all.
--
-- Every state below was probed live on 2026-09-11 through the same
-- `fetcher.list_jobs()` call the MCP surface uses.

-- Dead boards, no working replacement found. 404 on the registered board and
-- on the probed alternates (greenhouse/lever/ashby under the obvious slugs).
-- What the probe establishes is that the *board config* is unreachable; it
-- says nothing about whether the company still exists. Clearing the config
-- rather than the row keeps that distinction — the identity, name and bio
-- survive, and a pasted URL resolves the open question.
UPDATE companies SET ats = NULL, board = NULL
WHERE slug IN ('adept-ai', 'fly', 'groq', 'replicate', 'wandb');

-- Registered board 404s. The Lever board under the same slug answers 200 with
-- zero postings — indistinguishable by status code from a disabled posting
-- API, so repointing there would publish "no open roles" as if it were a fact.
-- Clear instead and let a pasted URL settle it.
UPDATE companies SET ats = NULL, board = NULL
WHERE slug = 'mistral';

-- Moved Greenhouse -> Ashby. Registered board 404s; ashby/thumbtack serves
-- 40 live postings as of 2026-09-11.
UPDATE companies SET ats = 'ashby', board = 'thumbtack'
WHERE slug = 'thumbtack';

-- Not a company: a test fixture that reached the production registry.
-- No jobs, no sync_status dependents, nothing to preserve.
DELETE FROM sync_status WHERE company_slug = 'testcorp';
DELETE FROM companies WHERE slug = 'testcorp';
