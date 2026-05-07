-- Tighten activity_log.account_id after 015a's backfill committed. Split
-- from 015a to clear pending FK trigger events between the UPDATE and
-- the DDL on the same table (PostgreSQL refuses ALTER TABLE / CREATE
-- INDEX while constraint trigger events are queued).

ALTER TABLE activity_log ALTER COLUMN account_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_log_account
    ON activity_log (account_id, log_date DESC);
