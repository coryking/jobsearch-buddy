-- Accounts: per-human identity row keyed on (provider, external_id).
--
-- The MCP server is the only path to authenticated activity. There is no
-- signup form — `accounts` rows appear lazily when an authenticated MCP
-- request arrives carrying a verified OAuth token, via UPSERT on
-- (provider, external_id). For Entra/AzureProvider, external_id is the
-- `oid` claim (stable per user across the tenant). For GitHub, it's `sub`
-- (stringified numeric user id). The provider column is set from
-- JOBBUDDY_AUTH_PROVIDER and tagged on every row so a future provider
-- swap doesn't collide with old rows.
--
-- The "bootstrap" account exists to own pre-migration activity_log rows.
-- After the operator's first authenticated MCP request creates their
-- real account, they manually re-attribute pre-existing rows:
--
--   UPDATE activity_log
--   SET account_id = (SELECT id FROM accounts WHERE provider='entra' AND external_id='<your-oid>')
--   WHERE account_id = (SELECT id FROM accounts WHERE provider='local' AND external_id='bootstrap');

CREATE TABLE IF NOT EXISTS accounts (
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    provider      TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    email         TEXT,
    display_name  TEXT,
    handle        TEXT,
    raw_claims    JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);

-- Stable bootstrap row. Same (provider, external_id) every time so the
-- INSERT is idempotent and downstream code can resolve it by lookup.
INSERT INTO accounts (provider, external_id, display_name)
VALUES ('local', 'bootstrap', 'Pre-auth history')
ON CONFLICT (provider, external_id) DO NOTHING;

-- Add nullable account_id, backfill every existing row to bootstrap.
-- The next migration tightens to NOT NULL and adds the index.
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id);

UPDATE activity_log
SET account_id = (SELECT id FROM accounts WHERE provider='local' AND external_id='bootstrap')
WHERE account_id IS NULL;
