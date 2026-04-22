CREATE TABLE IF NOT EXISTS query_embeddings (
    query_text TEXT NOT NULL,
    model      TEXT NOT NULL,
    embedding  vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (query_text, model)
);
