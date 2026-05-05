-- Phase 2: company bios for extract context.
--
-- Researched bios for each company. long_bio feeds the extract phase as
-- company context (so short_jd can elevate specifics-not-archetypes).
-- short_bio is generated alongside for free; no consumer today, future-
-- proofing for MCP exposure.
--
-- Population: ResearchPhase polls for long_bio IS NULL and fills in via
-- Azure Responses API + web_search.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS short_bio          TEXT,
    ADD COLUMN IF NOT EXISTS long_bio           TEXT,
    ADD COLUMN IF NOT EXISTS bio_researched_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bio_model          TEXT;
