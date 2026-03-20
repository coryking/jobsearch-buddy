-- Migration 007b: listing_status constraints + trigger + indexes
--
-- Runs after 007a (which backfilled listing_status). Separate transaction
-- so ALTER TABLE doesn't conflict with pending trigger events from the backfill.

-- NOT NULL + default after backfill
ALTER TABLE jobs ALTER COLUMN listing_status SET NOT NULL;
ALTER TABLE jobs ALTER COLUMN listing_status SET DEFAULT 'active';

-- Rename disappeared_at → removed_at
ALTER TABLE jobs RENAME COLUMN disappeared_at TO removed_at;

-- Trigger: auto-manage removed_at based on listing_status transitions
CREATE OR REPLACE FUNCTION manage_removed_at() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.listing_status = 'removed' AND (OLD IS NULL OR OLD.listing_status != 'removed') THEN
        NEW.removed_at = COALESCE(NEW.removed_at, NOW());
    ELSIF NEW.listing_status = 'active' THEN
        NEW.removed_at = NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_manage_removed_at
    BEFORE INSERT OR UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION manage_removed_at();

-- Drop old indexes that reference disappeared_at
DROP INDEX IF EXISTS idx_jobs_disappeared;
DROP INDEX IF EXISTS idx_jobs_needs_strip;
DROP INDEX IF EXISTS idx_jobs_needs_embed;
DROP INDEX IF EXISTS idx_jobs_embedding;

-- Active jobs index (replaces idx_jobs_disappeared)
CREATE INDEX idx_jobs_active ON jobs (listing_status) WHERE listing_status = 'active';

-- Strip phase polling
CREATE INDEX idx_jobs_needs_strip
    ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';
