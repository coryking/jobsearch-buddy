-- Migration 007e: NOT NULL on content_hash + job_embeddings table + data migration
--
-- Creates the job_embeddings table, migrates existing embeddings from
-- jobs.embedding, and drops the inline column. HNSW index is in 007f
-- (separate transaction — the INSERT fires trg_embedding_hash_consistency
-- which is DEFERRABLE INITIALLY DEFERRED, creating pending trigger events
-- that block CREATE INDEX on the same table).

-- Set NOT NULL now that backfill is done (separate transaction from the UPDATE)
ALTER TABLE jobs ALTER COLUMN content_hash SET NOT NULL;

-- ============================================================================
-- job_embeddings table
-- ============================================================================

CREATE TABLE job_embeddings (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    chunk_index     SMALLINT NOT NULL DEFAULT 0,
    job_hash        UUID NOT NULL,
    company_hash    UUID NOT NULL,
    embedding       vector(1536) NOT NULL,
    UNIQUE (job_id, chunk_index)
);

CREATE INDEX idx_embeddings_job_id ON job_embeddings (job_id);

-- Consistency constraint: all chunks for a job_id must share the same hashes.
-- DEFERRABLE INITIALLY DEFERRED so it checks at commit, not per-row.
CREATE OR REPLACE FUNCTION check_embedding_hash_consistency()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM job_embeddings
        WHERE job_id = NEW.job_id
        GROUP BY job_id
        HAVING COUNT(DISTINCT job_hash) > 1
            OR COUNT(DISTINCT company_hash) > 1
    ) THEN
        RAISE EXCEPTION 'job_embeddings has inconsistent hashes for job_id %', NEW.job_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_embedding_hash_consistency
    AFTER INSERT OR UPDATE ON job_embeddings
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION check_embedding_hash_consistency();

-- ============================================================================
-- Migrate existing embeddings from jobs → job_embeddings
-- ============================================================================

INSERT INTO job_embeddings (job_id, chunk_index, job_hash, company_hash, embedding)
SELECT j.id, 0, j.content_hash, c.content_hash, j.embedding
FROM jobs j
JOIN companies c ON j.company_slug = c.slug
WHERE j.embedding IS NOT NULL;

-- ============================================================================
-- Drop inline embedding column
-- ============================================================================

ALTER TABLE jobs DROP COLUMN embedding;

