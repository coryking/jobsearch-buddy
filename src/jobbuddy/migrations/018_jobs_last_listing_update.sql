-- Issue #53: per-fetcher last_listing_update.
--
-- Adds a second date column for ATSes that publicly expose a "last
-- updated" timestamp distinct from the original first-publish date.
-- See docs/architecture.md for the per-fetcher mapping.
--
-- Semantics:
--   published_at        -- what the ATS reports as the listing's publish
--                          date (first_published / createdAt / postedAt /
--                          sitemap lastmod, depending on platform).
--   last_listing_update -- what the ATS reports about freshness, where a
--                          distinct field exists. NULL for ATSes whose
--                          public API only exposes one date.
--
-- The store upsert preserves the latest non-NULL value via GREATEST() so a
-- subsequent sync that observes a newer ATS-side update overwrites an
-- older value, while a NULL from a fetcher that doesn't surface this
-- field never clobbers an existing value.

ALTER TABLE jobs ADD COLUMN last_listing_update DATE NULL;
