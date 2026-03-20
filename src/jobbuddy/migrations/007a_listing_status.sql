-- Migration 007a: listing_status enum + backfill + trigger
--
-- Split from 007 because PostgreSQL won't allow ALTER TABLE after an UPDATE
-- that generated pending trigger events (FK constraint checks) in the same
-- transaction. This file does the UPDATE; 007b does the remaining ALTERs.
--
-- Written against prod schema (001-005 only, 006 never applied).

-- 1. Create the enum type
CREATE TYPE listing_status AS ENUM ('active', 'removed');

-- 2. Add the column (nullable for now, backfill next)
ALTER TABLE jobs ADD COLUMN listing_status listing_status;

-- 3. Backfill from disappeared_at (single pass, CASE statement)
UPDATE jobs SET listing_status = CASE
    WHEN disappeared_at IS NULL THEN 'active'::listing_status
    ELSE 'removed'::listing_status
END;
