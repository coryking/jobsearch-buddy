-- Initial schema: jobs, sync_status, indexes, pgvector HNSW
-- Safe to run against existing databases (IF NOT EXISTS everywhere)
--
-- Prerequisite: pgvector extension must be installed by a superuser before
-- running migrations (CREATE EXTENSION vector requires superuser).

CREATE TABLE IF NOT EXISTS sync_status (
    company_slug    TEXT PRIMARY KEY,
    last_sync       TIMESTAMPTZ NOT NULL,
    job_count       INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  SERIAL PRIMARY KEY,
    company_slug        TEXT NOT NULL,
    job_id              TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT,
    url                 TEXT,
    published_at        DATE,
    department          TEXT,
    team                TEXT,
    salary              TEXT,
    description         TEXT,
    description_stripped TEXT CHECK(description_stripped IS NULL OR LENGTH(description_stripped) > 0),
    ats_metadata        JSONB,
    embedding           vector(1536),
    last_seen           TIMESTAMPTZ NOT NULL,
    disappeared_at      TIMESTAMPTZ,
    UNIQUE (company_slug, job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs (company_slug);
CREATE INDEX IF NOT EXISTS idx_jobs_published ON jobs (published_at);
CREATE INDEX IF NOT EXISTS idx_jobs_disappeared ON jobs (disappeared_at) WHERE disappeared_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_embedding ON jobs USING hnsw (embedding vector_cosine_ops);
