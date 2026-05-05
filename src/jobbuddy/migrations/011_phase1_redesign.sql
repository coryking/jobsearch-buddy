-- Phase 1 redesign: drop the embedding stack, replace strip with distill.
--
-- Schema changes (in dependency order):
--   1. Drop indexes/columns that reference description_stripped
--   2. Drop the job_embeddings stack (table, trigger function, query cache)
--   3. Drop content_hash columns (existed only for embedding cache invalidation)
--   4. Drop description_stripped
--   5. Add short_jd and description_normalized
--   6. Rebuild fts_vector over (title, short_jd, description_normalized, location, department)
--   7. Add B-tree on (published_at DESC, company_slug, job_id) for cursor pagination
--   8. Add partial index for the distill phase's polling predicate
--
-- All this fits in one transaction. trg_embedding_hash_consistency was the
-- DEFERRABLE constraint trigger that forced 007 to be split, but we're
-- DROPPING the table here so no INSERT/UPDATE pending events fire.
--
-- pgvector extension stays installed; companies table stays unchanged.
-- Phase 2 (company-side work) will use them.

-- 1. Drop the FTS generated column (depends on description_stripped) and its index
DROP INDEX IF EXISTS idx_jobs_fts_vector;
ALTER TABLE jobs DROP COLUMN IF EXISTS fts_vector;

-- Drop the strip-phase polling index (also depends on description_stripped)
DROP INDEX IF EXISTS idx_jobs_needs_strip;

-- 2. Drop the embedding stack
DROP TABLE IF EXISTS job_embeddings;
DROP FUNCTION IF EXISTS check_embedding_hash_consistency();
DROP TABLE IF EXISTS query_embeddings;

-- 3. Drop content_hash columns (used only for embedding cache invalidation)
ALTER TABLE jobs DROP COLUMN IF EXISTS content_hash;
ALTER TABLE companies DROP COLUMN IF EXISTS content_hash;

-- 4. Drop description_stripped
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_description_stripped_check;
ALTER TABLE jobs DROP COLUMN IF EXISTS description_stripped;

-- 5. Add the distill-phase output columns
ALTER TABLE jobs ADD COLUMN short_jd TEXT
    CHECK (short_jd IS NULL OR length(short_jd) > 0);
ALTER TABLE jobs ADD COLUMN description_normalized TEXT
    CHECK (description_normalized IS NULL OR length(description_normalized) > 0);

-- 6. Rebuild fts_vector over the new columns. Weights:
--    A = title (tightest signal)
--    B = short_jd (curated capsule)
--    B = description_normalized (full prose, post-distill)
--    C = location
--    D = department
ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(short_jd, '')), 'B')
        || setweight(to_tsvector('english', coalesce(description_normalized, '')), 'B')
        || setweight(to_tsvector('english', coalesce(location, '')), 'C')
        || setweight(to_tsvector('english', coalesce(department, '')), 'D')
    ) STORED;

CREATE INDEX idx_jobs_fts_vector ON jobs USING GIN (fts_vector);

-- 7. Default-ordering / cursor-pagination index for search_jobs.
--    (published_at DESC, company_slug, job_id) is the cursor tuple.
CREATE INDEX idx_jobs_published_pagination
    ON jobs (published_at DESC NULLS LAST, company_slug, job_id);

-- 8. Distill phase's polling predicate. Stable column-presence check
--    (short_jd IS NOT NULL) — no hash, avoids the Azure embedding-starvation
--    churn class.
CREATE INDEX idx_jobs_needs_distill
    ON jobs (id)
    WHERE description IS NOT NULL
      AND short_jd IS NULL
      AND listing_status = 'active';
