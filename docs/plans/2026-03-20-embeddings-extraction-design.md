# Embeddings Extraction & Cache Invalidation Design

## Problem

The `jobs` table has an inline `embedding vector(1536)` column with a 559 MB HNSW
index. Every metadata update (title, description, listing_status) creates a new row
version. With an 11% HOT update rate on Azure (89% of updates trigger full index
maintenance including the HNSW graph), even no-op upserts during sync are expensive.

Migration 006 (listing_status) took 23 minutes on Azure's B1ms instance before we
cancelled it — the backfill UPDATE touched every row and the HNSW index had to be
rebuilt for each one.

## Solution

Extract embeddings into a dedicated `job_embeddings` table. The HNSW index lives on
that table. The `jobs` table becomes narrow, with high HOT update rates. Metadata
upserts never touch the HNSW index.

Add content-hash-based cache invalidation so the embed phase only re-embeds jobs
whose inputs actually changed.

## Current State (as of 2026-03-20)

- PostgreSQL 18, pgvector 0.8.0 on Azure (just upgraded from 16)
- HNSW index dropped in prod (done manually to unblock the PG upgrade)
- Migration 006 (listing_status) never applied in prod (rolled back clean)
- Code on `main` expects 006 schema (listing_status, removed_at)
- Devbox has 006 applied + HNSW index intact
- ~94K total jobs, ~74K with embeddings, ~66K active

## Token Distribution (stripped descriptions)

Zero descriptions exceed the embedding model's 8,191 token context window.
Chunking for token overflow is not needed.

| Metric | Est. Tokens |
|--------|-------------|
| P50    | 1,163       |
| P75    | 1,484       |
| P90    | 1,799       |
| P95    | 1,993       |
| P99    | 2,431       |
| Max    | 6,641       |

92% of stripped descriptions are under 2,000 tokens.

---

## Target Schema

### companies (modified)

```sql
ALTER TABLE companies ADD COLUMN content_hash UUID;
```

`content_hash` = `md5(name)::uuid` today. When company profiles are added (industry,
stage, product focus, etc.), the hash covers all fields that feed into embeddings.
Updated by whatever writes company data.

### jobs (modified)

```sql
-- New column
ALTER TABLE jobs ADD COLUMN content_hash UUID;

-- From migration 006 (listing_status), not yet applied in prod:
-- listing_status enum, removed_at rename, trigger, indexes
-- See migration section below.

-- Removed column
ALTER TABLE jobs DROP COLUMN embedding;
```

`content_hash` = `md5(coalesce(description_stripped,'') || title || coalesce(location,'') || coalesce(department,''))::uuid`.
Updated by `upsert_jobs()` and `update_stripped_description()`.

### job_embeddings (new)

```sql
CREATE TABLE job_embeddings (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    chunk_index     SMALLINT NOT NULL DEFAULT 0,
    job_hash        UUID NOT NULL,
    company_hash    UUID NOT NULL,
    embedding       vector(1536) NOT NULL,
    UNIQUE (job_id, chunk_index)
);
```

- `chunk_index`: always 0 today (one embedding per job). Supports future
  multi-vector scenarios (chunked descriptions, company facet embeddings)
  without schema changes.
- `job_hash`: snapshot of `jobs.content_hash` at time of embedding.
- `company_hash`: snapshot of `companies.content_hash` at time of embedding.
- `ON DELETE CASCADE`: when a job is deleted, embeddings are cleaned up.

---

## Indexes

### job_embeddings

```sql
-- Vector similarity search (the whole point)
CREATE INDEX idx_embeddings_hnsw
    ON job_embeddings USING hnsw (embedding vector_cosine_ops);

-- FK lookup for JOIN from jobs → embeddings
-- (implicitly useful, PG doesn't auto-index FKs)
CREATE INDEX idx_embeddings_job_id
    ON job_embeddings (job_id);
```

Build the HNSW index with `SET maintenance_work_mem = '1GB'` to keep it in memory
(~750 MB needed for 94K vectors at 1536 dims).

### jobs (updated sync phase indexes)

```sql
-- Replaces idx_jobs_disappeared
CREATE INDEX idx_jobs_active
    ON jobs (listing_status) WHERE listing_status = 'active';

-- Strip phase polling: active jobs with description but no stripped version
CREATE INDEX idx_jobs_needs_strip
    ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';
```

The old `idx_jobs_needs_embed` (which checked `embedding IS NULL` on the jobs table)
is no longer needed — embed phase polling uses a JOIN to job_embeddings instead.

---

## Cache Invalidation Pattern

Embeddings are expensive to generate (OpenAI API calls). We use content-addressed
hashing to detect when re-embedding is actually needed.

### How it works

Each source of embedding input has its own content hash:

```
companies.content_hash  →  md5(name + future profile fields)::uuid
jobs.content_hash       →  md5(description_stripped + title + location + department)::uuid
```

When the embed phase creates an embedding, it snapshots both hashes:

```
job_embeddings.job_hash      = jobs.content_hash at embed time
job_embeddings.company_hash  = companies.content_hash at embed time
```

Staleness = mismatch between the current source hashes and the stored snapshots.

### Who updates source hashes

| Writer                          | Updates                | Trigger                        |
|---------------------------------|------------------------|--------------------------------|
| `upsert_jobs()`                 | `jobs.content_hash`    | title/location/dept changed    |
| `update_stripped_description()`  | `jobs.content_hash`    | new stripped text               |
| Future: company profile writer  | `companies.content_hash` | profile regenerated           |

Each writer only hashes its own data. No cross-table cascading. The embed phase
discovers all staleness on its next poll.

### What feeds into embed_text() today

```python
def embed_text(company_name, title, department, location, description_stripped):
    # "{company_name} — {title}"
    # "{department}, {location}"
    # ""
    # "{description_stripped}"
    parts = [f"{company_name} — {title}"]
    meta = ", ".join(filter(None, [department, location]))
    if meta:
        parts.append(meta)
    parts.append("")
    parts.append(description_stripped)
    return "\n".join(parts)
```

The job hash covers: description_stripped, title, location, department.
The company hash covers: company name (and future profile/industry data).
Together they cover all inputs to embed_text().

---

## Query Patterns

### Embed phase: poll for work

```sql
SELECT j.id, j.content_hash AS job_hash, c.content_hash AS company_hash,
       j.company_slug, j.job_id, j.title, j.department, j.location,
       j.description_stripped, c.name AS company_name
FROM jobs j
JOIN companies c ON j.company_slug = c.slug
LEFT JOIN job_embeddings e ON e.job_id = j.id AND e.chunk_index = 0
WHERE j.listing_status = 'active'
  AND j.description_stripped IS NOT NULL
  AND (
      e.id IS NULL                              -- no embedding yet
      OR e.job_hash != j.content_hash           -- job data changed
      OR e.company_hash != c.content_hash       -- company data changed
  )
```

Pure column comparison — no text processing, no hash computation at poll time.

### Semantic search (KNN)

```sql
SET hnsw.iterative_scan = relaxed_order;  -- pgvector 0.8.0: scan until enough results survive filters

SELECT e.embedding <=> %s::vector AS distance,
       j.*, s.last_sync, c.name AS company_name
FROM job_embeddings e
JOIN jobs j ON e.job_id = j.id
LEFT JOIN sync_status s ON j.company_slug = s.company_slug
LEFT JOIN companies c ON j.company_slug = c.slug
WHERE j.listing_status = 'active'
ORDER BY e.embedding <=> %s::vector
LIMIT %s;
```

The HNSW index scan runs on `job_embeddings`. The JOIN to `jobs` is a primary key
lookup per result row — negligible cost for 25-100 results. Iterative scanning
ensures the index keeps scanning past filtered-out results.

### Filtered search (company/title/location)

Same as above with additional WHERE clauses on `j.company_slug`, `j.title ILIKE`,
`j.location ILIKE`, `j.published_at >=`. Iterative scanning handles the
over-filtering case where filters eliminate HNSW candidates.

### Upsert with IS DISTINCT FROM guard

```sql
INSERT INTO jobs (company_slug, job_id, title, location, ..., content_hash, listing_status)
VALUES (%s, %s, %s, %s, ..., md5(...)::uuid, 'active')
ON CONFLICT(company_slug, job_id) DO UPDATE SET
    title = excluded.title,
    location = excluded.location,
    ...
    content_hash = excluded.content_hash,
    listing_status = 'active'
WHERE jobs.title IS DISTINCT FROM excluded.title
   OR jobs.location IS DISTINCT FROM excluded.location
   OR jobs.url IS DISTINCT FROM excluded.url
   OR jobs.published_at IS DISTINCT FROM excluded.published_at
   OR jobs.department IS DISTINCT FROM excluded.department
   OR jobs.team IS DISTINCT FROM excluded.team
   OR jobs.salary IS DISTINCT FROM excluded.salary
   OR jobs.description IS DISTINCT FROM excluded.description
   OR jobs.listing_status IS DISTINCT FROM excluded.listing_status;
```

When nothing changed, PostgreSQL skips the write entirely — no new tuple, no index
updates, no WAL. Combined with no HNSW on the jobs table, fetch-phase upserts
become nearly free for unchanged jobs.

---

## Future: Multi-Vector Scenarios

The schema supports these without changes:

### Chunked embeddings (if descriptions grow)

`chunk_index` 0, 1, 2... for text chunks of one job. Search uses over-fetch +
deduplicate-by-job pattern. Currently unnecessary (zero descriptions exceed 8K tokens).

### Company facet embeddings

Separate `company_embeddings` table (not `job_embeddings`) since one company profile
maps to many jobs. Search runs KNN against both tables, combines scores. The
`company_hash` on `job_embeddings` already detects when company data changes.

### Different embedding models/dimensions

The separate table makes model swaps easy: create new table with new `vector(N)`
dimension, re-embed, swap search queries, drop old table. The `jobs` table is
untouched.

---

## Key Research Findings

### HOT updates and HNSW (pgvector issue #875)

When PostgreSQL can't do a HOT (Heap-Only Tuple) update — because the page is full
or an indexed column exists — it updates ALL indexes on the table, including HNSW.
The HNSW graph update is ~100x more expensive than a B-tree update. With inline
embeddings, 89% of metadata updates on Azure triggered HNSW maintenance for an
unchanged vector. Separating the tables eliminates this entirely.

PG 16→17→18 did not change HOT mechanics. The last relevant improvement was PG 16's
BRIN summarizing index exception, which doesn't apply to HNSW.

### pgvector 0.8.0 iterative scans

`hnsw.iterative_scan = relaxed_order` solves the overfiltering problem where WHERE
clauses (listing_status, company, title ILIKE) eliminate HNSW candidates and return
fewer than K results. The index keeps scanning until enough results survive filtering.
Enable this on all search connections.

### Vector storage

pgvector uses `STORAGE = external` — vectors are TOASTed (stored out-of-line in a
TOAST table). The inline heap tuple only stores an 18-byte TOAST pointer. The HNSW
index stores the full vector data in its own pages. Index size is ~8 KB per vector
at 1536 dimensions (~559 MB for 70K vectors).

### ON CONFLICT DO UPDATE always writes

PostgreSQL creates a new row version even if all values are identical — there is no
built-in "skip if unchanged" optimization. The `IS DISTINCT FROM` WHERE guard is
required to prevent no-op writes.
