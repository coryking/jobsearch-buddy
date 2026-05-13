-- Watchlists: account-scoped saved searches (company roster + default filters).
--
-- A watchlist is a saved-search definition, not a job list. Job listings
-- live in `jobs` and are surfaced via search_jobs(watchlist=slug). The
-- watchlist's `filter` (query, location, posted_since, etc.) and member
-- companies become defaults that the caller can override per-call.
--
-- Account-scoped slugs: `ai_focus` means different things for different
-- humans. The UNIQUE (account_id, slug) constraint enforces uniqueness
-- per owner, not globally.
--
-- `companies.slug` is itself the surrogate key for companies (TEXT
-- PRIMARY KEY, no separate id column), so member FK targets slug
-- directly. Dead slugs in a watchlist are junk, not history — CASCADE
-- both ways.

CREATE TABLE watchlists (
    id          UUID PRIMARY KEY DEFAULT uuidv7(),
    account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    notes       TEXT,
    filter      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, slug)
);

CREATE INDEX idx_watchlists_account ON watchlists (account_id);

CREATE TABLE watchlist_members (
    watchlist_id  UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    company_slug  TEXT NOT NULL REFERENCES companies(slug) ON DELETE CASCADE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, company_slug)
);

CREATE INDEX idx_watchlist_members_company ON watchlist_members (company_slug);
