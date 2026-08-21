-- Remove company configs that are provably dead and have no corpus jobs.
--
-- testcorp: a test entry (slug "testcorp", name "Test Corp") that has never
--   accumulated real jobs and has been 404-ing on every sync. No FK references
--   in jobs (confirmed 2026-05-22).
--
-- retool: Retool Inc. was acquired by Brex and its product shut down. The
--   company row has ats=NULL/board=NULL with a stale Greenhouse URL in
--   sync_status from April 2026. No FK references in jobs.
--
-- FK chain: jobs.company_slug → sync_status.company_slug (migration 003).
-- sync_status has no FK to companies. Delete order: sync_status first (belt
-- and suspenders), then companies. Both are safe because 0 jobs reference
-- these slugs (verified 2026-05-22).

DELETE FROM sync_status WHERE company_slug IN ('testcorp', 'retool');
DELETE FROM companies WHERE slug IN ('testcorp', 'retool');
