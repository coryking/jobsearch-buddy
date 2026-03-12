-- FK: jobs.company_slug → sync_status.company_slug
-- DEFERRABLE so upsert_jobs can insert jobs before sync_status in same txn

DO $$ BEGIN
    ALTER TABLE jobs
        ADD CONSTRAINT fk_jobs_company_slug
        FOREIGN KEY (company_slug) REFERENCES sync_status(company_slug)
        DEFERRABLE INITIALLY DEFERRED;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
