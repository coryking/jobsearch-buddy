-- Full-text search GIN indexes for keyword search across title and description.
-- Per-field indexes (not a single generated column) so weights can be tuned at
-- query time without schema changes.

CREATE INDEX IF NOT EXISTS idx_jobs_title_fts
    ON jobs USING GIN (to_tsvector('english', coalesce(title, '')));

CREATE INDEX IF NOT EXISTS idx_jobs_desc_fts
    ON jobs USING GIN (to_tsvector('english', coalesce(description_stripped, '')));
