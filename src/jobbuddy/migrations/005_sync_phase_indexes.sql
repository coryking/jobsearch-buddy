-- Partial indexes for sync phase poll queries.
--
-- Strip and embed phases call count_remaining() + poll_work() repeatedly in a
-- tight producer loop. Without indexes, each call is a full seq scan (~20ms on
-- Azure with 90k+ rows). These partial indexes cover only the rows that actually
-- need work, so they stay small and are automatically excluded once jobs complete.

CREATE INDEX IF NOT EXISTS idx_jobs_needs_strip
    ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND disappeared_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_needs_embed
    ON jobs (id)
    WHERE description_stripped IS NOT NULL
      AND embedding IS NULL
      AND disappeared_at IS NULL;
