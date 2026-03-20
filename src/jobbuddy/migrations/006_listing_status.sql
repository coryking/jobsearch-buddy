-- Add listing_status enum and rename disappeared_at → removed_at

-- 1. Create the enum type
CREATE TYPE listing_status AS ENUM ('active', 'removed');

-- 2. Add the column (nullable for now, backfill next)
ALTER TABLE jobs ADD COLUMN listing_status listing_status;

-- 3. Backfill from disappeared_at (single pass)
UPDATE jobs SET listing_status = CASE
    WHEN disappeared_at IS NULL THEN 'active'::listing_status
    ELSE 'removed'::listing_status
END;

-- 4. NOT NULL + default after backfill
ALTER TABLE jobs ALTER COLUMN listing_status SET NOT NULL;
ALTER TABLE jobs ALTER COLUMN listing_status SET DEFAULT 'active';

-- 5. Rename disappeared_at → removed_at
ALTER TABLE jobs RENAME COLUMN disappeared_at TO removed_at;

-- 6. Trigger: auto-manage removed_at based on listing_status transitions
--    - When listing_status becomes 'removed': set removed_at = NOW() if it was NULL
--    - When listing_status becomes 'active': clear removed_at to NULL
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

-- 7. Drop old indexes, create new ones
DROP INDEX IF EXISTS idx_jobs_disappeared;
CREATE INDEX idx_jobs_active ON jobs (listing_status) WHERE listing_status = 'active';

-- 8. Recreate sync phase indexes to filter on listing_status instead of removed_at.
--    PostgreSQL won't match a query's "listing_status = 'active'" to an index's
--    "removed_at IS NULL" even though they're semantically equivalent.
DROP INDEX IF EXISTS idx_jobs_needs_strip;
CREATE INDEX idx_jobs_needs_strip
    ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';

DROP INDEX IF EXISTS idx_jobs_needs_embed;
CREATE INDEX idx_jobs_needs_embed
    ON jobs (id)
    WHERE description_stripped IS NOT NULL
      AND embedding IS NULL
      AND listing_status = 'active';
