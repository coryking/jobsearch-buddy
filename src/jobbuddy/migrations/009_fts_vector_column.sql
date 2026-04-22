-- Stored generated column for weighted full-text search.
-- Pre-computes the weighted tsvector so queries use it directly instead of
-- recomputing setweight(to_tsvector(...)) per row.
-- PG 18 defaults generated columns to VIRTUAL; we need STORED for GIN indexing.

ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(description_stripped, '')), 'B')
        || setweight(to_tsvector('english', coalesce(location, '')), 'C')
        || setweight(to_tsvector('english', coalesce(department, '')), 'D')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_jobs_fts_vector
    ON jobs USING GIN (fts_vector);

-- Drop the old per-field expression indexes (superseded by the combined column).
DROP INDEX IF EXISTS idx_jobs_title_fts;
DROP INDEX IF EXISTS idx_jobs_desc_fts;
