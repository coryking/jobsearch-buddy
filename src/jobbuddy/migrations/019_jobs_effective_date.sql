-- Issue #58: derived freshness date for filtering and ranking.
--
-- effective_date is COALESCE(last_listing_update, published_at) materialized
-- as a STORED generated column so search/ranking can index against it
-- without re-evaluating the COALESCE per row.
--
-- Why a generated column instead of inline COALESCE in queries:
--   1. We can index it. Sorting and `posted_after` filtering both want a
--      sortable date — the existing idx_jobs_published_pagination is on
--      published_at, which becomes the wrong key once the LLM expects
--      freshness semantics.
--   2. One source of truth. Every read path that wants "last sign of life"
--      reads the same column instead of re-deriving COALESCE, which drifts.
--   3. Backward compatibility. Rows whose fetcher doesn't surface
--      last_listing_update keep effective_date = published_at, so existing
--      reports/queries against published_at still match the same dates
--      they did before.
--
-- published_at is NOT NULL (migration 014), so effective_date is also
-- NOT NULL — no NULLS LAST needed in ORDER BY clauses.

ALTER TABLE jobs
  ADD COLUMN effective_date DATE
  GENERATED ALWAYS AS (COALESCE(last_listing_update, published_at)) STORED;

-- Replaces idx_jobs_published_pagination as the workhorse for paginated
-- recency queries. Old index stays in place — some scripts/reports still
-- want strict publish-date ordering.
CREATE INDEX idx_jobs_effective_pagination
  ON jobs (effective_date DESC, company_slug, job_id);
