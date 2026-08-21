-- Mark active jobs as removed when their parent company has no ATS fetcher
-- and the job was never enriched (description IS NULL). These jobs cannot
-- be re-fetched or distilled and pollute full-text search with title-only
-- records. Shape B companies (ats IS NULL, zero jobs) are unaffected.
UPDATE jobs
SET listing_status = 'removed',
    removed_at = NOW()
WHERE listing_status = 'active'
  AND description IS NULL
  AND company_slug IN (
      SELECT slug FROM companies WHERE ats IS NULL
  );
