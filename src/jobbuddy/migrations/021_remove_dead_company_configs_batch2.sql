-- Remove company configs that are provably dead and have no corpus jobs.
--
-- All six slugs below:
--   - return persistent 403 or 404 on every sync attempt
--   - have 0 rows in the jobs table (confirmed 2026-05-23)
--   - are unreachable because the company was acquired, the product was shut
--     down, or the ATS board moved to an unsupported platform
--
-- FK chain: jobs.company_slug → sync_status.company_slug (migration 003).
-- sync_status has no FK to companies. Delete order: sync_status first (belt
-- and suspenders), then companies. Both are safe because 0 jobs reference
-- these slugs (verified 2026-05-23).

DELETE FROM sync_status WHERE company_slug IN (
    'adept-ai',
    'fly',
    'groq',
    'replicate',
    'wandb',
    'wellsaid-labs'
);

DELETE FROM companies WHERE slug IN (
    'adept-ai',
    'fly',
    'groq',
    'replicate',
    'wandb',
    'wellsaid-labs'
);
