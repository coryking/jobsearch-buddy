-- Migration 007d: backfill jobs.content_hash
--
-- Separate from 007c (which added the column) to avoid potential
-- UPDATE + ALTER TABLE conflicts with pending trigger events.

UPDATE jobs SET content_hash = md5(
    coalesce(description_stripped, '')
    || title
    || coalesce(location, '')
    || coalesce(department, '')
)::uuid;
