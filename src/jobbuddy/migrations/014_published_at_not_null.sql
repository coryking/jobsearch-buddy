-- Issue #40: jobs.published_at NOT NULL.
--
-- Closes the shadow-suppression bug where rows with NULL published_at
-- always lost the recency tiebreak under `ORDER BY ... published_at DESC
-- NULLS LAST`. After this migration, the application can drop NULLS LAST
-- from every ranking query without changing semantics.
--
-- Pre-migration backfill is normally done by scripts/backfill_published_at.py
-- (which also recovers Avature sitemap dates that the pure-insert upsert
-- can't backfill from a routine sync). The UPDATE below is a safety net:
-- if any row is still NULL when the migration runs, it gets last_seen::date
-- so the constraint can land. last_seen is NOT NULL on every row.
--
-- DEFAULT CURRENT_DATE is belt-and-suspenders for INSERT paths that may
-- omit the column entirely. The application's upsert is explicit about
-- the column list and uses COALESCE(%s, CURRENT_DATE) in VALUES, so the
-- DEFAULT is rarely exercised — but it documents the contract in the
-- schema and protects ad-hoc scripts.

UPDATE jobs SET published_at = last_seen::date WHERE published_at IS NULL;

ALTER TABLE jobs ALTER COLUMN published_at SET DEFAULT CURRENT_DATE;
ALTER TABLE jobs ALTER COLUMN published_at SET NOT NULL;
