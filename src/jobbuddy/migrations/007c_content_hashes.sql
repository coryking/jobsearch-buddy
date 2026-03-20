-- Migration 007c: content hashes + job_embeddings table
--
-- Adds content_hash columns for cache invalidation, creates the job_embeddings
-- table, migrates existing embeddings, and drops the inline embedding column.
--
-- Split from 007b because UPDATE + ALTER TABLE in the same transaction fails
-- when FK constraint triggers generate pending events.

-- ============================================================================
-- Content hashes
-- ============================================================================

-- Companies (no trigger/FK issues — small table, no UPDATE+ALTER conflict)
ALTER TABLE companies ADD COLUMN content_hash UUID;
UPDATE companies SET content_hash = md5(name)::uuid;
ALTER TABLE companies ALTER COLUMN content_hash SET NOT NULL;

-- Jobs: add column (nullable)
ALTER TABLE jobs ADD COLUMN content_hash UUID;
