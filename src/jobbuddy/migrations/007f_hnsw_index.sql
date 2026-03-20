-- Migration 007f: HNSW index on job_embeddings
--
-- Separate from 007e because the INSERT that migrates embeddings fires
-- trg_embedding_hash_consistency (DEFERRABLE INITIALLY DEFERRED), creating
-- pending trigger events that block CREATE INDEX on the same table.

SET maintenance_work_mem = '1GB';
CREATE INDEX idx_embeddings_hnsw
    ON job_embeddings USING hnsw (embedding vector_cosine_ops);
RESET maintenance_work_mem;
