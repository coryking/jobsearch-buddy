CREATE TABLE IF NOT EXISTS query_embeddings (
    query_text TEXT PRIMARY KEY,
    embedding  vector(1536) NOT NULL,
    model      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
