-- Activity log table (migrated from CSV)

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL PRIMARY KEY,
    log_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    company     TEXT NOT NULL,
    role        TEXT NOT NULL,
    job_id      TEXT,
    action      TEXT,
    person      TEXT,
    location    TEXT,
    status      TEXT,
    url         TEXT,
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_activity_log_url ON activity_log (url) WHERE url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activity_log_company ON activity_log (lower(company));
CREATE INDEX IF NOT EXISTS idx_activity_log_date ON activity_log (log_date DESC);
