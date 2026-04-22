# PostgreSQL 18 Query Optimization Reference

Practical reference for PostgreSQL 18 query optimization, written for the
jobsearch-buddy codebase. The database runs on Azure Flexible Server (burstable
2-vCore tier) with pgvector 0.8.0. The `jobs` table has ~78K rows; the
`job_embeddings` table has a similar count. Workload is batch-write (sync)
and interactive-read (search queries).

---

## 1. Reading EXPLAIN ANALYZE

### Running EXPLAIN

Always use `EXPLAIN (ANALYZE, BUFFERS)` for real performance analysis. In PG 18,
`ANALYZE` automatically includes `BUFFERS` output (previously required as a
separate option). Add `FORMAT TEXT` for human reading, `FORMAT JSON` for tools
like pgMustard or explain.dalibo.com.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT j.*, c.name AS company_name
FROM jobs j
LEFT JOIN companies c ON j.company_slug = c.slug
WHERE j.fts_vector @@ websearch_to_tsquery('english', 'software engineer')
  AND j.listing_status = 'active'
ORDER BY ts_rank_cd(j.fts_vector, websearch_to_tsquery('english', 'software engineer'), 32) DESC
LIMIT 25;
```

### Reading the Output

Each node shows estimated and actual numbers:

```
Limit  (cost=152.42..152.48 rows=25 width=488) (actual time=12.340..12.355 rows=25 loops=1)
  Buffers: shared hit=1042 read=38
```

| Field | Meaning |
|-------|---------|
| `cost=152.42..152.48` | Estimated startup..total cost (arbitrary units, not milliseconds) |
| `rows=25` (estimated) | Planner's guess at row count |
| `rows=25` (actual) | Real rows returned |
| `actual time=12.340..12.355` | Milliseconds for first row..last row |
| `loops=1` | How many times this node executed (multiply actual values by loops) |
| `Buffers: shared hit=1042` | 8KB pages found in shared_buffers (no disk I/O) |
| `Buffers: shared read=38` | 8KB pages read from OS cache or disk |

**Multiply by loops.** An Index Scan inside a Nested Loop with `loops=100` and
`actual time=0.010..0.015` spent 100 * 0.015 = 1.5ms total, not 0.015ms.

**PG 18 addition:** `Index Searches: N` shows how many times a scan restarted
in the index. This replaces guessing from loops alone.

### Node Types

#### Scan Nodes (leaf nodes, read data)

| Node | When Used | What It Means |
|------|-----------|---------------|
| **Seq Scan** | No usable index, or table is small enough that sequential read is cheaper | Reads every row. Fine for <1K rows or heavily filtered tables. Problem at 78K rows. |
| **Index Scan** | B-tree/HNSW index with selective conditions | Walks the index, fetches heap rows one by one. Best for highly selective queries (few rows). |
| **Index Only Scan** | All needed columns are in the index + visibility map is fresh | Skips the heap entirely. The fastest scan type. |
| **Bitmap Index Scan** | Moderate selectivity (too many rows for Index Scan, too few for Seq Scan) | Builds a bitmap of matching pages. Always paired with Bitmap Heap Scan. |
| **Bitmap Heap Scan** | Follows a Bitmap Index Scan | Reads heap pages in physical order using the bitmap. `Heap Blocks: exact=N` is good; `lossy=N` means work_mem was too small for the bitmap. |

GIN indexes (FTS, JSONB, arrays) always produce Bitmap Index Scan plans.
They cannot do plain Index Scan.

#### Join Nodes

| Node | When Chosen | Characteristics |
|------|-------------|-----------------|
| **Nested Loop** | Small outer set, indexed inner lookup | For each outer row, scan inner. Fast when outer is small. Disastrous when outer is large and inner has no index. |
| **Hash Join** | Larger sets, equality joins | Builds hash table from smaller relation, probes with larger. Watch for `Batches: N` > 1 (spilled to disk because work_mem was too small). PG 18 improved performance and reduced memory usage of hash joins. |
| **Merge Join** | Both inputs already sorted (or cheaply sortable) | Walks both sorted inputs in lockstep. PG 18 allows merge joins to use incremental sorts. |

#### Other Common Nodes

| Node | Purpose |
|------|---------|
| **Sort** | ORDER BY. Check `Sort Method: quicksort Memory: NkB` (in-memory) vs `external merge Disk: NkB` (spilled). |
| **Materialize** | Caches child output for re-reading. PG 18 reports memory/disk usage. |
| **CTE Scan** | Reads from a materialized CTE. PG 18 reports memory/disk usage. |
| **Aggregate** | GROUP BY or aggregate functions. |
| **WindowAgg** | Window functions (ROW_NUMBER, etc.). |

### Spotting Problems

| Symptom | Likely Cause |
|---------|--------------|
| `actual rows` >> `estimated rows` | Stale statistics. Run `ANALYZE tablename`. |
| `actual rows` << `estimated rows` | Stale statistics, or correlated columns confusing the planner. |
| `Buffers: shared read` >> `shared hit` | Cold cache. Run the query again to see warm-cache performance. |
| `Sort Method: external merge Disk: NkB` | Sort spilled to disk. Increase `work_mem` (per-query with `SET LOCAL`). |
| `Hash Batches: 8` (or any N > 1) | Hash table spilled to disk. Increase `work_mem`. |
| `Bitmap Heap Scan ... Heap Blocks: lossy=N` | Bitmap exceeded work_mem, fell back to page-level tracking. Increase `work_mem`. |
| Seq Scan on a table where you expected an index scan | Missing index, expression mismatch, or planner decided seq scan was cheaper (check selectivity). |
| `Nested Loop` with high loops count on inner | May be correct (if inner is indexed), but check if a Hash Join would be faster. |

### EXPLAIN Options Reference

```sql
-- Full diagnostic output (PG 18)
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) SELECT ...;

-- SETTINGS shows non-default GUCs affecting the plan (work_mem, etc.)
-- VERBOSE adds output column lists and schema-qualified names
-- COSTS false suppresses cost estimates (useful for regression testing plans)
-- TIMING false suppresses per-node timing (reduces overhead for profiling)
```

---

## 2. Index Types and When to Use Each

### B-tree (Default)

The workhorse. Supports `=`, `<`, `>`, `<=`, `>=`, `BETWEEN`, `IN`, `IS NULL`,
pattern matching with anchored `LIKE 'prefix%'`.

```sql
-- Simple B-tree
CREATE INDEX idx_jobs_company ON jobs (company_slug);

-- Composite: leftmost prefix rule applies
CREATE INDEX idx_jobs_company_published ON jobs (company_slug, published_at DESC);
-- Useful for: WHERE company_slug = 'x' ORDER BY published_at DESC
-- Also useful for: WHERE company_slug = 'x' (uses first column)
-- NOT useful for: WHERE published_at > '2025-01-01' alone (skips first column)
-- BUT in PG 18: skip scan can help even when first column is omitted
```

**PG 18 skip scan:** Multi-column B-tree indexes can now be used even when
queries omit equality conditions on leading columns, provided the leading
columns have low cardinality. The planner "skips" through distinct values of
the omitted columns. This works best when the leading column has few distinct
values (like `listing_status` with 2 values, or `company_slug` with ~200).

```sql
-- PG 18 can use this index for WHERE published_at > '2025-01-01'
-- by skip-scanning through the ~200 distinct company_slug values
CREATE INDEX idx_jobs_company_published ON jobs (company_slug, published_at);
```

**PG 18 OR optimization:** OR/IN clauses can be transformed into array
operations for faster index processing.

### GIN (Generalized Inverted Index)

Inverted index for multi-valued columns: `tsvector`, `jsonb`, arrays.

```sql
-- Full-text search (what we use)
CREATE INDEX idx_jobs_fts_vector ON jobs USING GIN (fts_vector);

-- JSONB containment
CREATE INDEX idx_jobs_metadata ON jobs USING GIN (ats_metadata jsonb_path_ops);
```

GIN characteristics:
- Always produces Bitmap Index Scan (never plain Index Scan)
- 3x faster lookups than GiST, but slower to build and update
- Defers updates to a pending list (`gin_pending_list_limit`, default 4MB)
- **PG 18: parallel GIN index creation** is now supported

When to use: FTS with `@@`, JSONB with `@>` or `?`, array `@>` or `&&`.

### GiST (Generalized Search Tree)

Lossy index (may produce false positives, requires heap recheck). Supports
overlap, containment, proximity, nearest-neighbor.

```sql
-- Range types
CREATE INDEX idx_salary_range ON jobs USING GiST (salary_range);

-- PostGIS (if we ever add coordinates)
CREATE INDEX idx_location_geo ON jobs USING GiST (coordinates);
```

When to use: Geometric data, range types, exclusion constraints, nearest-neighbor
on non-vector data. For FTS, only when write speed matters more than read speed.

### HNSW (pgvector)

Approximate nearest-neighbor for vector similarity search.

```sql
-- What we use for embeddings
CREATE INDEX idx_embeddings_hnsw
    ON job_embeddings USING hnsw (embedding vector_cosine_ops);
```

Not a core PG index type -- provided by the pgvector extension. Parameters:
`m` (connections per node, default 16), `ef_construction` (build quality,
default 64). Higher values = better recall, slower builds, larger index.

### BRIN (Block Range Index)

Stores min/max summaries per range of physical blocks. Tiny index, useful
when data is physically ordered by the indexed column.

```sql
-- Good if jobs are inserted roughly in published_at order
CREATE INDEX idx_jobs_published_brin ON jobs USING BRIN (published_at);
```

When to use: Time-series data, append-only tables with millions of rows,
columns with high physical correlation. At 78K rows, BRIN offers little
advantage over B-tree. Consider it if the table grows to 1M+.

### Hash

Equality-only (`=`). Slightly faster than B-tree for pure equality, but
doesn't support range queries, sorting, or multicolumn indexes.

```sql
CREATE INDEX idx_jobs_status_hash ON jobs USING HASH (listing_status);
```

When to use: Almost never. B-tree handles equality fine and is more versatile.
Hash indexes became crash-safe in PG 10 but remain niche.

### Partial Indexes

Index a subset of rows. Dramatically smaller when filtering on a skewed column.

```sql
-- Only index active jobs (the ones we actually query)
CREATE INDEX idx_jobs_active_company ON jobs (company_slug)
    WHERE listing_status = 'active';

-- Only index jobs needing work (the sync phase poll pattern)
CREATE INDEX idx_jobs_needs_strip ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';
```

Partial indexes are one of the highest-leverage optimizations for this codebase.
The sync phase uses them extensively (migration 005). They work because:
- The WHERE clause matches the query's WHERE clause
- The index stays small (only unprocessed rows)
- As rows get processed, the index shrinks automatically

### Expression Indexes

Index the result of a function or expression.

```sql
-- Index on lowercased company name
CREATE INDEX idx_activity_company_lower ON activity_log (lower(company));

-- Index on computed tsvector (what migration 008 originally used)
CREATE INDEX idx_jobs_title_fts
    ON jobs USING GIN (to_tsvector('english', coalesce(title, '')));
```

The query must use the exact same expression for the index to be used.
This is a common source of "index not used" bugs.

### Composite Indexes

Order matters. The leftmost-prefix rule means a composite index on
`(a, b, c)` supports queries on `(a)`, `(a, b)`, and `(a, b, c)`,
but not `(b)` or `(c)` alone (though PG 18 skip scan relaxes this for
low-cardinality leading columns).

Column order guidelines:
1. Put equality-filtered columns first
2. Put range-filtered or ORDER BY columns last
3. Most selective column first (when all are equality)

```sql
-- For: WHERE company_slug = %s AND listing_status = 'active' ORDER BY published_at DESC
CREATE INDEX idx_jobs_company_status_published
    ON jobs (company_slug, listing_status, published_at DESC);
```

---

## 3. Index-Only Scans and Covering Indexes

### How Index-Only Scans Work

Normally, an index scan finds a row's location (TID), then fetches the full
row from the heap. An index-only scan skips the heap fetch entirely when:

1. All columns the query needs are in the index
2. The visibility map confirms the heap page is all-visible (all rows visible
   to all transactions)

Check for index-only scans in EXPLAIN output:

```
Index Only Scan using idx_jobs_company_published on jobs
  Index Cond: (company_slug = 'greenhouse')
  Heap Fetches: 12
```

`Heap Fetches: 0` is ideal -- all data came from the index. Non-zero means
some pages weren't all-visible (recently modified, not yet vacuumed).

### The Visibility Map

VACUUM maintains the visibility map. After bulk updates (like `jsb sync`),
pages won't be all-visible until VACUUM runs. Autovacuum handles this, but
if you need immediate index-only scan performance:

```sql
VACUUM jobs;
```

### Covering Indexes with INCLUDE

The `INCLUDE` clause adds payload columns to the index that aren't part of
the search key. These columns enable index-only scans without affecting
index ordering or size as much as adding them to the key.

```sql
-- Query: SELECT id, title FROM jobs WHERE company_slug = 'x' AND listing_status = 'active'
-- Covering index:
CREATE INDEX idx_jobs_company_covering
    ON jobs (company_slug)
    INCLUDE (id, title)
    WHERE listing_status = 'active';
```

INCLUDE columns:
- Are stored in leaf pages only (not internal pages), so index navigation isn't affected
- Cannot be used for filtering or sorting (only the key columns can)
- Support index-only scans for the included columns
- Work with B-tree and GiST indexes (not GIN, BRIN, or Hash)

### When Covering Indexes Help

For this codebase, the most impactful use would be on hot query paths where
we only need a few columns:

```sql
-- The sync poll query only needs id
-- This partial index already covers it:
CREATE INDEX idx_jobs_needs_strip ON jobs (id)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';

-- If we needed title too:
CREATE INDEX idx_jobs_needs_strip ON jobs (id)
    INCLUDE (title, company_slug)
    WHERE description IS NOT NULL
      AND description_stripped IS NULL
      AND listing_status = 'active';
```

---

## 4. The Pre-Limit Pattern

This is the pattern that fixed the 75-second hybrid search query.

### The Problem

When a query uses `ORDER BY function(column) LIMIT N`, PostgreSQL must
evaluate the function on every matching row before it can sort and limit.
There's no index on `ts_rank_cd(fts_vector, query)` because the function
depends on the query parameter.

```sql
-- Slow: scores all 25K FTS matches, sorts, takes top 25
SELECT j.*, ts_rank_cd(j.fts_vector, query, 32) AS score
FROM jobs j
WHERE j.fts_vector @@ websearch_to_tsquery('english', 'engineer')
ORDER BY score DESC
LIMIT 25;
```

With 25K matches, `ts_rank_cd()` runs 25K times, each requiring a heap fetch
to read the tsvector. On a burstable Azure tier with limited I/O, that's 75 seconds.

### The Fix: Pre-Limit with a CTE or Subquery

Limit the candidate set before scoring:

```sql
-- Fast: GIN narrows to 25K, takes first 200, scores only those
WITH candidates AS (
    SELECT j.*
    FROM jobs j
    WHERE j.fts_vector @@ websearch_to_tsquery('english', 'engineer')
      AND j.listing_status = 'active'
    LIMIT 200
)
SELECT *, ts_rank_cd(fts_vector, websearch_to_tsquery('english', 'engineer'), 32) AS score
FROM candidates
ORDER BY score DESC
LIMIT 25;
```

This drops from 25K function evaluations to 200. The 200 candidates come from
whatever order the GIN bitmap scan returns them (roughly physical order), so
ranking precision trades off against speed.

### Tradeoffs

| Approach | Speed | Ranking Quality | When to Use |
|----------|-------|-----------------|-------------|
| Score all matches | Slow | Perfect | Small result sets (<1K matches) |
| Pre-limit then score | Fast | Approximate | Large result sets (>1K matches) |
| Pre-limit + ORDER BY indexed column | Fast | Better | If you can pre-sort by a useful proxy (e.g., published_at DESC) |

The pre-limit candidate quality depends on what order the GIN scan returns rows.
You can improve it by adding a secondary sort before the limit:

```sql
WITH candidates AS (
    SELECT j.*
    FROM jobs j
    WHERE j.fts_vector @@ websearch_to_tsquery('english', 'engineer')
      AND j.listing_status = 'active'
    ORDER BY j.published_at DESC NULLS LAST  -- prefer recent jobs
    LIMIT 200
)
SELECT *, ts_rank_cd(fts_vector, websearch_to_tsquery('english', 'engineer'), 32) AS score
FROM candidates
ORDER BY score DESC
LIMIT 25;
```

### Hybrid Search Application

For hybrid search with RRF, both the FTS and vector branches should pre-limit
independently before merging:

```sql
WITH fts_candidates AS (
    SELECT j.id
    FROM jobs j
    WHERE j.fts_vector @@ websearch_to_tsquery('english', $1)
      AND j.listing_status = 'active'
    LIMIT 200  -- pre-limit before scoring
),
fts_ranked AS (
    SELECT c.id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank_cd(j.fts_vector,
                   websearch_to_tsquery('english', $1), 32) DESC
           ) AS rank_ix
    FROM fts_candidates c
    JOIN jobs j ON c.id = j.id
),
semantic AS (
    SELECT e.job_id AS id,
           ROW_NUMBER() OVER (ORDER BY e.embedding <=> $2::vector) AS rank_ix
    FROM job_embeddings e
    JOIN jobs j ON e.job_id = j.id
    WHERE j.listing_status = 'active'
    ORDER BY e.embedding <=> $2::vector
    LIMIT 200
)
SELECT j.*,
    COALESCE(1.0 / (50 + f.rank_ix), 0.0)
  + COALESCE(1.0 / (50 + s.rank_ix), 0.0) AS rrf_score
FROM fts_ranked f
FULL OUTER JOIN semantic s ON f.id = s.id
JOIN jobs j ON COALESCE(f.id, s.id) = j.id
ORDER BY rrf_score DESC
LIMIT 25;
```

---

## 5. CTE Optimization

### PG 12+ Inlining Rules

Before PG 12, CTEs were always materialized (an "optimization fence"). Since
PG 12, the planner may inline a CTE into the outer query if ALL of these are true:

1. Non-recursive
2. No side effects (not INSERT/UPDATE/DELETE with RETURNING)
3. Referenced only once in the outer query
4. Not self-referencing

When inlined, the CTE behaves like a subquery -- the planner can push
predicates into it, reorder joins, and choose indexes normally.

### Explicit Control

```sql
-- Force materialization (useful as an optimization fence)
WITH candidates AS MATERIALIZED (
    SELECT id FROM jobs
    WHERE fts_vector @@ websearch_to_tsquery('english', 'engineer')
    LIMIT 200
)
SELECT ... FROM candidates ...;

-- Force inlining (rare, for multiply-referenced CTEs)
WITH stats AS NOT MATERIALIZED (
    SELECT company_slug, COUNT(*) AS cnt FROM jobs GROUP BY company_slug
)
SELECT ... FROM stats s1 JOIN stats s2 ...;
```

### When CTEs Help Performance

**As an optimization fence (MATERIALIZED):** Use when the planner makes bad
decisions after inlining. A materialized CTE with LIMIT forces the planner
to respect the limit before subsequent operations.

This is exactly the pre-limit pattern from Section 4. Without MATERIALIZED,
PG might inline the CTE and push the ORDER BY ts_rank_cd() through,
defeating the purpose. In practice, PG respects LIMIT in subqueries, but
explicitly marking MATERIALIZED makes the intent clear and prevents future
planner changes from breaking the optimization.

**As a readability tool (auto-inlined):** Single-use CTEs that add readability
without performance cost. The planner inlines them, so they perform identically
to subqueries.

### When CTEs Hurt Performance

**Multiply-referenced materialized CTEs:** If a CTE is referenced twice, it
materializes. For large result sets, this means computing and storing the entire
result in memory (or temp files). Consider whether the repeated computation is
actually cheaper than materialization.

**PG 18 addition:** EXPLAIN ANALYZE now reports memory and disk usage for
CTE Scan nodes, making it easy to spot expensive materializations:

```
CTE Scan on candidates  (cost=... rows=200 width=488) (actual time=... rows=200 loops=1)
  Storage: Memory  Maximum Storage: 256kB
```

If you see `Storage: Disk`, the CTE spilled to temp files.

---

## 6. Join Optimization

### When PG Chooses Each Join Strategy

| Join Type | Chosen When | Strengths | Weaknesses |
|-----------|-------------|-----------|------------|
| **Nested Loop** | Small outer set, or inner has a good index | Low startup cost, works with any join condition | O(n*m) without inner index |
| **Hash Join** | Equality join, smaller relation fits in work_mem | Fast for medium-to-large equi-joins | Needs equality condition, memory-sensitive |
| **Merge Join** | Both inputs sortable/sorted, equality join | Handles very large inputs, no memory limit | Requires sorted input (or sort cost) |

### Join Order and join_collapse_limit

PG's planner evaluates different join orders to find the cheapest plan.
`join_collapse_limit` (default 8) controls how many tables the planner will
consider reordering. With more tables than this limit, it uses the order
written in the query.

For this codebase, queries join at most 3-4 tables (`jobs`, `companies`,
`sync_status`, `job_embeddings`), well within the default. No tuning needed.

### Common Join Issues

**FULL OUTER JOIN in RRF:** The hybrid search pattern uses FULL OUTER JOIN
to merge FTS and vector results. This prevents the planner from choosing
Nested Loop (which requires one side to be the "outer"). PG typically uses
Hash Join for FULL OUTER JOIN, which is fine for small result sets (200 rows).

**Missing statistics on join columns:** If `jobs.company_slug` has stale
stats, PG might misestimate the join with `companies` and choose a Seq Scan
on `companies` instead of an index lookup. Fix with `ANALYZE companies`.

**Implicit type casts in join conditions:** Joining `integer` to `bigint`
or `text` to `varchar` can prevent index usage if PG inserts a cast function.
Ensure join column types match exactly.

### PG 18 Join Improvements

- Hash join performance improved, memory usage reduced
- Merge joins can use incremental sorts (sort only the unsorted portion of
  an already partially-sorted input)
- Self-join elimination: unnecessary self-joins are automatically removed
- Right Semi Join plans for existence checks (IN/EXISTS subqueries)

---

## 7. Statistics and the Planner

### How the Planner Estimates Rows

The planner relies on `pg_statistic` (exposed via `pg_stats`) to estimate
how many rows will match a condition. Key statistics:

| Statistic | What It Contains | Affects |
|-----------|-----------------|---------|
| `n_distinct` | Number of distinct values (or negative fraction) | Equality selectivity |
| `most_common_vals` + `most_common_freqs` | Top N values and their frequencies | Equality estimates for skewed columns |
| `histogram_bounds` | Equal-depth histogram of non-MCV values | Range query estimates |
| `correlation` | Physical vs logical ordering (-1 to 1) | Index scan vs bitmap scan cost |
| `null_frac` | Fraction of NULL values | IS NULL / IS NOT NULL estimates |

### Checking Statistics

```sql
-- See statistics for a column
SELECT attname, n_distinct, most_common_vals, most_common_freqs,
       correlation, null_frac
FROM pg_stats
WHERE tablename = 'jobs' AND attname = 'company_slug';

-- Check when ANALYZE last ran
SELECT relname, last_analyze, last_autoanalyze, n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE relname = 'jobs';
```

### When Statistics Go Wrong

**After bulk operations:** `jsb sync` upserts thousands of rows. Until
ANALYZE runs, the planner's row estimates may be wildly off, causing:
- Nested Loop where Hash Join would be faster
- Seq Scan where Index Scan would be faster
- Sort instead of Index Scan for ORDER BY

Fix: Run `ANALYZE jobs;` after sync, or rely on autovacuum (which triggers
ANALYZE after 10% of rows change, controlled by
`autovacuum_analyze_scale_factor`).

### Increasing Statistics Resolution

The default `default_statistics_target` is 100 (100 MCV entries, 100
histogram buckets). For columns with many distinct values or skewed
distributions, increase it:

```sql
-- Increase statistics target for company_slug (200+ distinct values)
ALTER TABLE jobs ALTER COLUMN company_slug SET STATISTICS 500;
ANALYZE jobs;
```

Higher targets mean ANALYZE takes longer and plans take slightly longer to
generate, but row estimates become more accurate. For 78K rows, even
`SET STATISTICS 1000` is cheap.

### PG 18 Statistics Improvements

- `pg_restore_relation_stats()` and `pg_restore_attribute_stats()` allow
  direct manipulation of planner statistics (useful for testing plan behavior)
- `pg_upgrade` now preserves optimizer statistics across major version
  upgrades, preventing the post-upgrade performance dip that previously
  required running ANALYZE on every table

---

## 8. work_mem and Sort Spills

### What work_mem Controls

`work_mem` sets the maximum memory for individual sort operations, hash
tables, and bitmap heaps before they spill to temp files on disk. Each
operation in a query gets its own allocation, and each backend session gets
its own allocations, so total memory usage can far exceed `work_mem`.

Default: 4MB. Azure Flexible Server burstable tier has ~2GB RAM total with
~512MB for shared_buffers, leaving limited headroom.

### Detecting Spills in EXPLAIN

```sql
-- Sort spill
Sort  (cost=... rows=25000 width=488) (actual time=... rows=25000 loops=1)
  Sort Key: score DESC
  Sort Method: external merge  Disk: 12288kB  -- SPILLED TO DISK
  Buffers: shared hit=..., temp read=1536 written=1536

-- Hash spill
Hash Join  (cost=... rows=10000 width=488) (actual time=... rows=10000 loops=1)
  Hash Cond: (j.company_slug = c.slug)
  ->  Hash  (cost=... rows=200 width=36) (actual time=... rows=200 loops=1)
        Buckets: 1024  Batches: 4  -- SPILLED: Batches > 1 means disk
        Memory Usage: 4096kB
```

Look for:
- `Sort Method: external merge` (vs `quicksort` for in-memory)
- `Hash Batches: N` where N > 1
- `temp read=N written=N` in BUFFERS output
- `Bitmap Heap Scan ... Heap Blocks: lossy=N`

### Per-Query work_mem Tuning

Don't increase `work_mem` globally on a memory-constrained server. Use
`SET LOCAL` inside a transaction:

```sql
BEGIN;
SET LOCAL work_mem = '32MB';
-- Your expensive query here
SELECT ...;
COMMIT;  -- work_mem reverts to default
```

Or with autocommit (psycopg pattern):

```python
with store.conn.transaction():
    store.conn.execute("SET LOCAL work_mem = '32MB'")
    rows = store.conn.execute(expensive_query, params).fetchall()
# work_mem reverts after transaction ends
```

### Guidelines for This Codebase

| Operation | Likely work_mem Need | Reason |
|-----------|---------------------|--------|
| FTS search (25 matches) | 4MB (default) | Small result set |
| FTS search (25K matches, pre-limit) | 4MB (default) | Pre-limit keeps set small |
| Vector search (HNSW) | 4MB (default) | HNSW returns K results directly |
| Sync phase polls | 4MB (default) | Simple queries on partial indexes |
| Full table sort | 8-16MB | 78K rows * ~500B avg width = ~39MB uncompressed |

Monitor with `log_temp_files = 0` to log all temp file usage, or check
`pg_stat_statements` for queries with high `temp_blks_read`.

---

## 9. Buffer Cache and I/O

### How the Buffer Cache Works

PostgreSQL reads 8KB pages from disk into shared_buffers (the buffer cache).
Subsequent reads of the same page are "hits" (no disk I/O). The OS filesystem
cache provides a second layer.

```
Query → shared_buffers (hit?) → OS page cache (hit?) → disk (read)
```

`effective_cache_size` tells the planner how much total cache to expect
(shared_buffers + OS cache). It doesn't allocate memory -- it's a hint that
affects cost estimates for index scans vs seq scans.

### Reading Buffer Statistics

```
Buffers: shared hit=1042 read=38 dirtied=0 written=0
```

| Counter | Meaning |
|---------|---------|
| `shared hit` | Pages found in shared_buffers. Free. |
| `shared read` | Pages fetched from OS or disk. Expensive (especially on Azure burstable). |
| `shared dirtied` | Pages modified (will need writing later). |
| `shared written` | Pages written back to disk during this query (rare, usually checkpointer does this). |
| `temp read/written` | Temp file I/O for sorts/hashes that spilled. Very expensive. |

### Warm vs Cold Cache

First query execution may show high `read` counts. Run the query twice to
see warm-cache performance. Production performance depends on whether the
working set fits in shared_buffers.

For this codebase: 78K jobs at ~2KB average row = ~156MB. Plus indexes.
With 512MB shared_buffers, the entire working set fits comfortably. Vector
search is the exception -- 78K * 1536 * 4 bytes = ~457MB of embeddings, so
the HNSW index likely doesn't fit entirely in shared_buffers.

### Azure Burstable Tier I/O Constraints

The burstable B-series tier has:
- **Credit-based CPU:** Sustained computation drains credits, leading to throttling
- **Limited IOPS:** Lower baseline IOPS than General Purpose
- **No PgBouncer:** Built-in connection pooling unavailable
- **No intelligent tuning:** Azure's auto-tuning features disabled
- **Managed shared_buffers:** Azure manages this parameter; cannot be modified directly

Implications for query optimization:
- Random I/O (index lookups on cold pages) is proportionally more expensive
- Pre-limiting candidate sets (Section 4) matters more than on higher tiers
- Queries that cause sort spills are disproportionately painful
- VACUUM timing matters -- stale visibility maps force heap fetches

### PG 18 Async I/O

The new async I/O subsystem queues multiple read requests, allowing the OS to
optimize I/O scheduling. Configuration:

```sql
-- io_method: 'sync' (legacy), 'worker' (thread-based), 'io_uring' (Linux kernel)
-- Azure Flexible Server likely defaults to 'worker' or 'sync'
SHOW io_method;

-- I/O concurrency (default raised to 16 in PG 18)
SHOW effective_io_concurrency;       -- for user queries
SHOW maintenance_io_concurrency;     -- for VACUUM, CREATE INDEX, etc.

-- Combine limit: how many pages to combine into one I/O request
SHOW io_combine_limit;
```

Bitmap Heap Scan (which GIN always produces) benefits significantly from async
I/O because it reads scattered heap pages that can be prefetched in parallel.

Real-world benchmarks show 20-25% throughput improvement on Azure Premium SSD.
On burstable tier with standard SSD, gains may be lower but tail latency
(P99) improvements of 30-50% have been reported across properly configured
systems.

---

## 10. PG 18 Specific Optimizations

### Summary of Performance-Relevant PG 18 Features

| Feature | Impact for This Codebase |
|---------|--------------------------|
| **Async I/O** | Faster GIN bitmap heap scans, HNSW index reads, VACUUM |
| **B-tree skip scan** | Could benefit queries on `(company_slug, published_at)` when filtering only by date |
| **OR → array transformation** | Faster queries with OR/IN on indexed columns |
| **Hash join improvements** | Faster joins with reduced memory, relevant for hybrid search FULL OUTER JOIN |
| **Merge join + incremental sort** | Merge joins can leverage partial ordering |
| **Parallel GIN index build** | Faster `CREATE INDEX` on fts_vector after migrations |
| **EXPLAIN auto-BUFFERS** | No more forgetting the BUFFERS flag |
| **EXPLAIN fractional rows** | More precise estimates visible in plans |
| **Statistics preservation in pg_upgrade** | No performance cliff after major version upgrade |
| **Virtual generated columns (default)** | Must explicitly say `STORED` for GIN-indexed tsvector columns |
| **Self-join elimination** | Automatic, no action needed |

### Virtual Generated Columns

PG 18 changed the default for generated columns from STORED to VIRTUAL.
Virtual columns compute at read time and use no disk space, but cannot be
indexed with GIN (or any index type that needs materialized data).

The `fts_vector` column (migration 009) must be `STORED`:

```sql
-- Correct in PG 18
ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (...) STORED;  -- STORED is required

-- This would FAIL with GIN:
ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (...);  -- defaults to VIRTUAL in PG 18, can't GIN-index
```

### EXPLAIN Enhancements

PG 18 EXPLAIN ANALYZE output improvements:
- **Automatic BUFFERS**: No need to add `BUFFERS` separately
- **Index Searches**: Shows number of index restarts per scan node
- **Fractional rows**: `rows=3.50` instead of rounding to `rows=4`
- **Memory/disk for Material, CTE Scan, WindowAgg**: Shows storage type and size
- **Parallel bitmap worker stats**: Cache statistics per worker
- **Disabled node indication**: Shows when a node was disabled by the planner

---

## 11. Common Anti-Patterns

### SELECT *

Fetching all columns prevents index-only scans and increases I/O. In this
codebase, `store.py` queries use `SELECT j.*, s.last_sync, c.name` -- which
is reasonable since callers generally need the full job record. But for poll
queries (sync phase), select only needed columns:

```sql
-- Anti-pattern (fetches entire row for a poll)
SELECT * FROM jobs WHERE description IS NULL AND listing_status = 'active';

-- Better (only what the sync phase needs)
SELECT id, company_slug, job_id, title, ats_metadata
FROM jobs WHERE description IS NULL AND listing_status = 'active';
```

### Functions in WHERE Defeating Indexes

```sql
-- Anti-pattern: LOWER() prevents B-tree index usage
WHERE LOWER(company_slug) = 'amazon'

-- Fix: expression index, or store pre-lowered
CREATE INDEX idx_jobs_company_lower ON jobs (LOWER(company_slug));
-- OR just ensure data is always lowercase
```

### Implicit Type Casts

```sql
-- Anti-pattern: job_id is TEXT, but passing an integer
WHERE job_id = 12345  -- PG casts job_id to integer, or adds a cast function

-- Fix: pass the right type
WHERE job_id = '12345'
```

### Correlated Subqueries

```sql
-- Anti-pattern: runs subquery per row
SELECT *,
    (SELECT COUNT(*) FROM job_embeddings e WHERE e.job_id = j.id) AS embed_count
FROM jobs j;

-- Better: join or lateral
SELECT j.*, COALESCE(e.cnt, 0) AS embed_count
FROM jobs j
LEFT JOIN (SELECT job_id, COUNT(*) AS cnt FROM job_embeddings GROUP BY job_id) e
    ON e.job_id = j.id;
```

### Over-Indexing

Each index:
- Slows down INSERT/UPDATE/DELETE (must update index)
- Consumes disk space and shared_buffers
- Increases VACUUM work

For 78K rows, the marginal cost is low, but still audit periodically.
A table with 15 indexes on it is a red flag.

### Unnecessary DISTINCT

```sql
-- Anti-pattern: DISTINCT as a crutch for duplicate joins
SELECT DISTINCT j.* FROM jobs j
JOIN companies c ON j.company_slug = c.slug;

-- If companies has unique slugs, no DISTINCT needed
SELECT j.* FROM jobs j
JOIN companies c ON j.company_slug = c.slug;
```

### N+1 Query Patterns

Not a SQL anti-pattern per se, but common in application code:

```python
# Anti-pattern
for company in companies:
    jobs = store.conn.execute("SELECT * FROM jobs WHERE company_slug = %s", (company,))

# Better: single query with IN
store.conn.execute("SELECT * FROM jobs WHERE company_slug = ANY(%s)", (company_list,))
```

---

## 12. Practical Diagnostic Queries

### Finding Slow Queries (pg_stat_statements)

```sql
-- Enable the extension (one-time, requires superuser)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Top 10 queries by total execution time
SELECT
    calls,
    round(total_exec_time::numeric, 1) AS total_ms,
    round(mean_exec_time::numeric, 1) AS mean_ms,
    round(stddev_exec_time::numeric, 1) AS stddev_ms,
    rows,
    query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;

-- Queries with worst average time
SELECT
    calls,
    round(mean_exec_time::numeric, 1) AS mean_ms,
    round((stddev_exec_time / NULLIF(mean_exec_time, 0))::numeric, 2) AS cv,
    rows,
    left(query, 120) AS query
FROM pg_stat_statements
WHERE calls > 5
ORDER BY mean_exec_time DESC
LIMIT 10;

-- Queries that spill to temp files
SELECT
    calls,
    round(mean_exec_time::numeric, 1) AS mean_ms,
    temp_blks_read + temp_blks_written AS temp_blks,
    left(query, 120) AS query
FROM pg_stat_statements
WHERE temp_blks_read + temp_blks_written > 0
ORDER BY temp_blks_read + temp_blks_written DESC
LIMIT 10;

-- Reset stats (do this periodically to keep stats fresh)
SELECT pg_stat_statements_reset();
```

### Finding Unused Indexes

```sql
-- Indexes that have never been scanned (candidates for removal)
SELECT
    s.schemaname,
    s.relname AS table_name,
    s.indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
    s.idx_scan AS times_scanned
FROM pg_stat_user_indexes s
WHERE s.idx_scan = 0
  AND s.indexrelname NOT LIKE '%_pkey'  -- keep primary keys
  AND s.indexrelname NOT LIKE '%unique%'  -- keep unique constraints
ORDER BY pg_relation_size(s.indexrelid) DESC;
```

### Finding Tables with Sequential Scans

```sql
-- Tables where seq scans dominate (potential missing indexes)
SELECT
    relname,
    seq_scan,
    idx_scan,
    CASE WHEN seq_scan + idx_scan > 0
         THEN round(100.0 * seq_scan / (seq_scan + idx_scan), 1)
         ELSE 0 END AS seq_scan_pct,
    n_live_tup AS row_count
FROM pg_stat_user_tables
WHERE n_live_tup > 1000  -- ignore small tables
ORDER BY seq_scan_pct DESC, seq_scan DESC;
```

### Table and Index Sizes

```sql
-- Table and index sizes
SELECT
    t.relname AS table_name,
    pg_size_pretty(pg_total_relation_size(t.relid)) AS total_size,
    pg_size_pretty(pg_table_size(t.relid)) AS table_size,
    pg_size_pretty(pg_indexes_size(t.relid)) AS index_size,
    n_live_tup AS row_count,
    n_dead_tup AS dead_rows
FROM pg_stat_user_tables t
ORDER BY pg_total_relation_size(t.relid) DESC;
```

### Table Bloat Detection

```sql
-- Rough bloat estimate based on dead tuples
SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    CASE WHEN n_live_tup > 0
         THEN round(100.0 * n_dead_tup / n_live_tup, 1)
         ELSE 0 END AS dead_pct,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY dead_pct DESC;
```

### Index Health Check

```sql
-- Index sizes and usage for a specific table
SELECT
    i.indexrelname AS index_name,
    i.idx_scan AS scans,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS size,
    am.amname AS type,
    pi.indexdef
FROM pg_stat_user_indexes i
JOIN pg_index idx ON i.indexrelid = idx.indexrelid
JOIN pg_class c ON i.indexrelid = c.oid
JOIN pg_am am ON c.relam = am.oid
JOIN pg_indexes pi ON i.indexrelname = pi.indexname
WHERE i.relname = 'jobs'
ORDER BY pg_relation_size(i.indexrelid) DESC;
```

### Cache Hit Ratio

```sql
-- Overall cache hit ratio (should be >99% for a well-tuned system)
SELECT
    sum(heap_blks_hit) AS heap_hits,
    sum(heap_blks_read) AS heap_reads,
    CASE WHEN sum(heap_blks_hit) + sum(heap_blks_read) > 0
         THEN round(100.0 * sum(heap_blks_hit) /
              (sum(heap_blks_hit) + sum(heap_blks_read)), 2)
         ELSE 100 END AS hit_ratio_pct
FROM pg_statio_user_tables;

-- Index cache hit ratio
SELECT
    sum(idx_blks_hit) AS index_hits,
    sum(idx_blks_read) AS index_reads,
    CASE WHEN sum(idx_blks_hit) + sum(idx_blks_read) > 0
         THEN round(100.0 * sum(idx_blks_hit) /
              (sum(idx_blks_hit) + sum(idx_blks_read)), 2)
         ELSE 100 END AS hit_ratio_pct
FROM pg_statio_user_indexes;
```

### Stale Statistics Detection

```sql
-- Tables where row count has changed significantly since last analyze
SELECT
    relname,
    n_live_tup AS current_rows,
    n_mod_since_analyze AS mods_since_analyze,
    last_analyze,
    last_autoanalyze,
    CASE WHEN n_live_tup > 0
         THEN round(100.0 * n_mod_since_analyze / n_live_tup, 1)
         ELSE 0 END AS pct_changed
FROM pg_stat_user_tables
WHERE n_mod_since_analyze > 1000
ORDER BY pct_changed DESC;
```

---

## Appendix: Quick Reference for jobsearch-buddy

### Current Indexes

| Index | Type | Table | Purpose |
|-------|------|-------|---------|
| `jobs_pkey` | B-tree | jobs | Primary key (id) |
| `jobs_company_slug_job_id_key` | B-tree | jobs | Unique constraint (company_slug, job_id) |
| `idx_jobs_company` | B-tree | jobs | Company lookup |
| `idx_jobs_published` | B-tree | jobs | Date ordering |
| `idx_jobs_fts_vector` | GIN | jobs | Full-text search |
| `idx_jobs_needs_strip` | B-tree (partial) | jobs | Sync strip phase poll |
| `idx_jobs_needs_embed` | B-tree (partial) | jobs | Sync embed phase poll |
| `idx_embeddings_hnsw` | HNSW | job_embeddings | Vector similarity |
| `idx_embeddings_job_id` | B-tree | job_embeddings | FK lookup |

### Post-Sync Maintenance Checklist

```sql
-- After jsb sync, run these to ensure fresh statistics
ANALYZE jobs;
ANALYZE job_embeddings;

-- Check GIN pending list (if queries seem slow after bulk insert)
SELECT gin_clean_pending_list('idx_jobs_fts_vector');

-- Check for bloat after large upserts
SELECT relname, n_dead_tup, last_autovacuum
FROM pg_stat_user_tables WHERE relname IN ('jobs', 'job_embeddings');
```

### The 80/20 Optimization Checklist

1. Run `EXPLAIN (ANALYZE, BUFFERS)` on the slow query
2. Check: Is an index being used? If not, why? (expression mismatch, type cast, stale stats)
3. Check: Are sorts/hashes spilling? (`external merge`, `Batches > 1`, `temp read/written`)
4. Check: Are row estimates wildly off? Run `ANALYZE`.
5. Check: Is the query scoring/ranking too many rows? Apply the pre-limit pattern.
6. Check: Is the buffer cache cold? Run the query twice. If second run is fast, it's a cold-cache issue.
7. Only after 1-6: consider new indexes, schema changes, or GUC tuning.
