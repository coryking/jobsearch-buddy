-- Phase 2: company bio embeddings for find_companies.
--
-- Adds a 1536-dim embedding of long_bio and an HNSW cosine index. Mirrors
-- the dropped 007e/007f stack but on companies, not jobs, and without the
-- DEFERRABLE-trigger dance (no upstream content_hash, so one migration is
-- enough).
--
-- Population: EmbedPhase polls long_bio IS NOT NULL AND
-- (bio_embedding IS NULL OR bio_embedding_updated_at < bio_researched_at)
-- and writes the vector + timestamp via the existing OpenAI client
-- (text-embedding-3-small).
--
-- pgvector extension is already installed (see 001 / 011 comments).

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS bio_embedding             vector(1536),
    ADD COLUMN IF NOT EXISTS bio_embedding_updated_at  TIMESTAMPTZ;

-- HNSW index for cosine similarity search. maintenance_work_mem boost
-- mirrors the (dropped) idx_embeddings_hnsw recipe — 700 rows is tiny but
-- the same RAM pattern keeps build deterministic across environments.
SET maintenance_work_mem = '1GB';
CREATE INDEX IF NOT EXISTS idx_companies_bio_embedding_hnsw
    ON companies USING hnsw (bio_embedding vector_cosine_ops);
RESET maintenance_work_mem;
