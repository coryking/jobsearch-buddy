# PostgreSQL Full-Text Search Reference

Practical reference for PostgreSQL 18 full-text search (FTS), written for the
jobsearch-buddy codebase where FTS complements pgvector semantic search. The
database is PostgreSQL 18 with pgvector 0.8.0 on Azure.

---

## 1. tsvector and tsquery Fundamentals

### tsvector: The Document Representation

A `tsvector` is a sorted list of normalized lexemes (stemmed words) with
optional position and weight information. PostgreSQL strips stop words, applies
stemming, deduplicates, and records positions.

```sql
SELECT to_tsvector('english', 'Senior Software Engineer at Cloudflare in Austin, TX');
-- 'austin':6 'cloudflar':4 'engin':3 'senior':1 'softwar':2 'tx':7
```

Key behaviors:
- Stop words ("at", "in") are removed -- their positions are skipped
- Words are stemmed ("Engineer" -> "engin", "Software" -> "softwar")
- Positions are preserved (matters for phrase search and cover density ranking)
- Each lexeme appears once, with a list of positions

Concatenating tsvectors preserves weights and merges positions:

```sql
SELECT setweight(to_tsvector('english', 'Software Engineer'), 'A')
    || setweight(to_tsvector('english', 'Build distributed systems at scale'), 'B');
-- 'build':3B 'distribut':4B 'engin':2A 'scale':6B 'softwar':1A 'system':5B
```

### tsquery: The Query Representation

A `tsquery` is a boolean expression of lexemes connected by operators:

| Operator | Meaning | Example |
|----------|---------|---------|
| `&` | AND | `'engineer' & 'senior'` |
| `\|` | OR | `'python' \| 'golang'` |
| `!` | NOT | `!'intern'` |
| `<->` | FOLLOWED BY (adjacent) | `'software' <-> 'engineer'` |
| `<N>` | FOLLOWED BY (distance N) | `'product' <2> 'manager'` |

The FOLLOWED BY operators require positional information in the tsvector. `<->`
is shorthand for `<1>` (immediately adjacent). `<N>` allows exactly N-1 words
between the terms. Stop words consume positions, so `phraseto_tsquery` accounts
for them:

```sql
SELECT phraseto_tsquery('english', 'the head of engineering');
-- 'head' <2> 'engin'
-- Position gap of 2 accounts for the stop word "of"
```

### Four Ways to Build a tsquery

| Function | Input Style | Default Operator | Errors on Bad Input | Best For |
|----------|-------------|------------------|---------------------|----------|
| `to_tsquery` | Structured (operators required) | User-specified | Yes | Programmatic queries |
| `plainto_tsquery` | Plain text | `&` (AND) | Never | Simple keyword search |
| `phraseto_tsquery` | Plain text | `<->` (FOLLOWED BY) | Never | Exact phrase matching |
| `websearch_to_tsquery` | Web search syntax | Smart | Never | User-facing search boxes |

**`websearch_to_tsquery`** is the right choice for user input. It supports:

```sql
SELECT websearch_to_tsquery('english', 'senior engineer');
-- 'senior' & 'engin'                    (unquoted = AND)

SELECT websearch_to_tsquery('english', '"software engineer"');
-- 'softwar' <-> 'engin'                 (quoted = phrase)

SELECT websearch_to_tsquery('english', 'engineer or manager');
-- 'engin' | 'manag'                     (OR = |)

SELECT websearch_to_tsquery('english', 'engineer -intern');
-- 'engin' & !'intern'                   (dash = NOT)

SELECT websearch_to_tsquery('english', '"product manager" or "program manager"');
-- 'product' <-> 'manag' | 'program' <-> 'manag'
```

It never raises syntax errors, making it safe for raw user input. It does not
support weight labels or prefix matching (use `to_tsquery` for those).

**`to_tsquery`** is the most powerful but requires valid operator syntax:

```sql
SELECT to_tsquery('english', 'Fat & Rats:AB');
-- 'fat' & 'rat':AB                      (weight filter)

SELECT to_tsquery('english', 'super:*');
-- 'super':*                              (prefix match)
```

### The @@ Match Operator

```sql
-- Does the document match the query?
SELECT to_tsvector('english', 'Senior Software Engineer') @@
       websearch_to_tsquery('english', 'software engineer');
-- true
```

---

## 2. Ranking Functions

### ts_rank()

Ranks based on **frequency** of matching lexemes. Higher frequency = higher score.

```sql
ts_rank([ weights float4[], ] vector tsvector, query tsquery [, normalization integer ])
  returns float4
```

### ts_rank_cd()

Ranks based on **cover density** -- how close together the matching lexemes are
in the document. A document where "software" and "engineer" are adjacent scores
higher than one where they are 200 words apart.

```sql
ts_rank_cd([ weights float4[], ] vector tsvector, query tsquery [, normalization integer ])
  returns float4
```

**For job search, `ts_rank_cd` is the better choice** because proximity matters:
"Software Engineer" in a title is more relevant than a description that mentions
"software" in paragraph 1 and "engineer" in paragraph 5.

### Normalization Flags (Bitmask)

The optional integer argument is a bitmask that controls how document length
affects the score. Combine flags with `|`. Transformations apply in the order
listed.

| Flag | Effect | Use When |
|------|--------|----------|
| 0 | Ignore document length (default) | All documents are similar length |
| 1 | Divide by `1 + log(document_length)` | Gentle length penalty |
| 2 | Divide by `document_length` | Strong length penalty |
| 4 | Divide by mean harmonic distance between extents (`ts_rank_cd` only) | Want proximity to dominate |
| 8 | Divide by number of unique words | Penalize vocabulary-rich documents |
| 16 | Divide by `1 + log(unique_word_count)` | Gentle vocabulary penalty |
| 32 | Divide by `rank + 1` | Scale all scores to 0..1 range |

**Practical guidance for job search:**

```sql
-- Flag 32: normalize scores to 0..1 for combining with other signals
SELECT ts_rank_cd(fts_vector, query, 32) AS fts_score ...

-- Flag 1|32: gentle length normalization + 0..1 scaling
-- Prevents long job descriptions from dominating short ones
SELECT ts_rank_cd(fts_vector, query, 1|32) AS fts_score ...
```

Flag 32 is almost always useful when combining FTS scores with other ranking
signals (like vector similarity) because it bounds the output range. Without it,
ts_rank scores are unbounded positive floats that vary wildly across documents.

Flag 2 (divide by document length) is usually too aggressive for job
descriptions -- a 2000-word description that matches well should not score 10x
lower than a 200-word stub.

---

## 3. Weighting with setweight()

### Assigning Weights

`setweight()` labels tsvector entries with A, B, C, or D. Unlabeled entries
default to D.

```sql
setweight(vector tsvector, weight "char") returns tsvector
```

For job listings, a natural weight mapping:

```sql
-- Title matches matter most, description matches matter, location/department less so
setweight(to_tsvector('english', coalesce(title, '')), 'A')
|| setweight(to_tsvector('english', coalesce(description_stripped, '')), 'B')
|| setweight(to_tsvector('english', coalesce(location, '')), 'C')
|| setweight(to_tsvector('english', coalesce(department, '')), 'D')
```

### Default Weight Multipliers

The `ts_rank` and `ts_rank_cd` functions apply these multipliers by default:

| Weight | Default Multiplier | Typical Use |
|--------|--------------------|-------------|
| A | 1.0 | Title, name |
| B | 0.4 | Description, body |
| C | 0.2 | Metadata (location, tags) |
| D | 0.1 | Low-signal fields |

The ratio matters more than the absolute values. With defaults, a title match
(A) counts 10x more than a department match (D) and 2.5x more than a
description match (B).

### Custom Weight Arrays

Override defaults by passing a `float4[]` as the first argument. The array
order is `{D, C, B, A}` (counterintuitive -- D is first):

```sql
-- Make title matches 5x description, ignore location/department
SELECT ts_rank_cd(
    '{0.0, 0.0, 0.2, 1.0}'::float4[],    -- {D, C, B, A}
    fts_vector,
    query,
    32    -- normalize to 0..1
) AS score
FROM ...
```

### Weight Tuning Strategy

1. Start with defaults `{0.1, 0.2, 0.4, 1.0}`
2. Run representative queries and inspect which results rank unexpectedly
3. If description matches drown out title matches, increase the A/B ratio
4. If location matches pull in irrelevant results, lower C weight
5. Use the eval framework to measure changes (same approach as strip prompt eval)

**GIN indexes do not store weight labels.** Weight-based ranking always requires
a heap fetch to read the actual tsvector. This means weighting adds no index
overhead but does require the tsvector to be materialized (stored column or
expression recomputation).

---

## 4. Index Types for Full-Text Search

### GIN (Generalized Inverted Index) -- The Default Choice

```sql
-- Expression index (what we use today)
CREATE INDEX idx_jobs_title_fts
    ON jobs USING GIN (to_tsvector('english', coalesce(title, '')));

-- On a stored tsvector column
CREATE INDEX idx_jobs_fts ON jobs USING GIN (fts_vector);

-- Concurrent creation (no table lock, safe for production)
CREATE INDEX CONCURRENTLY idx_jobs_fts ON jobs USING GIN (fts_vector);
```

GIN is an inverted index: one entry per lexeme pointing to a compressed list of
matching row locations. It is **not lossy** -- no false positives from the index
itself.

| Property | GIN |
|----------|-----|
| Lookup speed | ~3x faster than GiST |
| Build time | ~3x slower than GiST |
| Update speed | ~10x slower than GiST |
| Index size | ~2-3x larger than GiST |
| Lossy | No |
| Scan type | Bitmap Index Scan only (never Index Scan) |

**Fastupdate and the pending list:** By default, GIN defers index updates into
a pending list (controlled by `gin_pending_list_limit`, default 4MB). This
amortizes write cost but means:
- Queries must scan both the main index and the pending list
- Pending list cleanup happens on vacuum, when the limit is reached, or via
  `gin_clean_pending_list()`
- High-write tables can see latency spikes when the pending list flushes

For jobsearch-buddy this is not a concern -- writes happen in batch during
`jsb sync`, not under continuous load.

**Build optimization:** Increase `maintenance_work_mem` before building:

```sql
SET maintenance_work_mem = '512MB';
CREATE INDEX CONCURRENTLY idx_jobs_fts ON jobs USING GIN (fts_vector);
RESET maintenance_work_mem;
```

### GiST (Generalized Search Tree) -- The Niche Choice

```sql
CREATE INDEX idx_jobs_fts_gist ON jobs
    USING GIST (fts_vector tsvector_ops(siglen = 256));
```

GiST represents each document as a fixed-length bit signature (default 124
bytes, max 2024). Words are hashed into bit positions and OR-ed together.

| Property | GiST |
|----------|------|
| Lossy | Yes (false positives require heap recheck) |
| Build time | Faster than GIN |
| Update speed | Faster than GIN |
| Index size | Smaller than GIN |
| Tunable | `siglen` parameter (longer = fewer false positives, larger index) |

**When to use GiST instead of GIN:**
- Very high write throughput where update speed dominates
- Tables with <100K unique lexemes
- Need covering indexes (GiST supports `INCLUDE`)

**For jobsearch-buddy, GIN is the correct choice.** The job cache is
write-infrequent (batch sync) and read-frequent (search queries). GIN's 3x
faster lookups matter more than its slower updates.

### Expression Index vs Stored Column

The codebase uses **expression indexes** (migration 008):

```sql
-- Expression index: no extra column, recomputes to_tsvector on heap fetch
CREATE INDEX idx_jobs_title_fts
    ON jobs USING GIN (to_tsvector('english', coalesce(title, '')));
```

The alternative is a **stored generated column** with a plain GIN index:

```sql
-- Stored column: precomputed tsvector, no recomputation on read
ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(description_stripped, '')), 'B')
        || setweight(to_tsvector('english', coalesce(location, '')), 'C')
        || setweight(to_tsvector('english', coalesce(department, '')), 'D')
    ) STORED;

CREATE INDEX idx_jobs_fts ON jobs USING GIN (fts_vector);
```

Trade-offs:

| | Expression Index | Stored Column |
|---|---|---|
| Disk space | Smaller (no extra column) | Larger (tsvector materialized) |
| Read perf | Recomputes `to_tsvector` on heap fetch | No recomputation |
| Write perf | Faster writes | Slower writes (maintain column) |
| Weights | Cannot use `setweight` in expression | Can use `setweight` |
| Config in query | Must specify `'english'` in every query | Baked into column |

**If you want weighted ranking (title > description > location), you need
the stored column approach.** Expression indexes cannot store weight labels
because each index entry covers only one expression.

### PG 18: Virtual Generated Columns

PostgreSQL 18 makes `VIRTUAL` the default for generated columns. Virtual
columns compute values at read time and do not consume disk space. However,
**you cannot create a GIN index on a virtual generated column** -- the column
must be `STORED` (or use an expression index). Virtual generated columns are
irrelevant for FTS indexing.

---

## 5. Text Search Configurations

### Built-in Configurations

**`'english'`** -- The standard choice for English text:
- Applies the Snowball English stemmer ("engineering" -> "engin")
- Removes English stop words ("the", "is", "at", "in", ...)
- Handles common English morphology

**`'simple'`** -- Minimal processing:
- Lowercases only (no stemming)
- No stop word removal (unless configured)
- "Engineering" -> "engineering" (not "engin")

```sql
SELECT to_tsvector('english', 'Senior Software Engineering Manager');
-- 'engin':3 'manag':4 'senior':1 'softwar':2

SELECT to_tsvector('simple', 'Senior Software Engineering Manager');
-- 'engineering':3 'manager':4 'senior':1 'software':2
```

### When to Use Each

| Config | Pros | Cons | Use For |
|--------|------|------|---------|
| `english` | Matches morphological variants ("manage" matches "manager", "managing") | Over-stems some technical terms; stop word removal loses some signal | Description search |
| `simple` | Exact token matching; preserves all words | No morphological matching | Proper nouns, company names, exact terms |

For job search, **`'english'` is the right default** for titles and descriptions.
Consider `'simple'` for company names and locations where stemming is harmful
(you do not want "Amazon" stemmed).

### How the Dictionary Chain Works

Each configuration maps token types to an ordered list of dictionaries.
Processing is first-match-wins:

1. Token is passed to the first dictionary
2. If the dictionary returns a lexeme -> use it, stop
3. If it returns an empty array -> token is a stop word, discard it
4. If it returns NULL -> pass to the next dictionary
5. The Snowball stemmer should always be last (it accepts everything)

The `english` configuration chain for word tokens is typically:
`english_stem` (Snowball stemmer with English stop words)

### Custom Configuration Example

For job search, you might want a configuration that handles tech jargon:

```sql
-- Create a synonym dictionary for common abbreviations
-- File: $SHAREDIR/tsearch_data/tech_synonyms.syn
-- Contents:
--   k8s    kubernetes
--   js     javascript
--   ts     typescript
--   ml     machinelearning
--   ai     artificialintelligence
--   devops developmentoperations
--   sre    sitereliabilityengineering
--   pm     productmanager
--   tpm    technicalprogrammanager

CREATE TEXT SEARCH DICTIONARY tech_synonyms (
    TEMPLATE = synonym,
    SYNONYMS = tech_synonyms
);

CREATE TEXT SEARCH CONFIGURATION jobsearch (COPY = pg_catalog.english);

ALTER TEXT SEARCH CONFIGURATION jobsearch
    ALTER MAPPING FOR asciiword, asciihword, hword_asciipart
    WITH tech_synonyms, english_stem;
```

This is high-effort, low-payoff for a personal tool. Stick with `'english'`
unless specific queries consistently miss due to abbreviations.

### Debugging Configurations with ts_debug

```sql
SELECT * FROM ts_debug('english', 'Senior Software Engineer in Austin TX');
```

Returns each token, which dictionary matched it, and the resulting lexeme.
Essential for understanding why a query does or does not match.

---

## 6. Combining FTS with Vector Similarity (Hybrid Search)

### Why Hybrid Search

FTS and vector search have complementary strengths:

| | Full-Text Search | Vector Search |
|---|---|---|
| Exact terms | Excellent ("kubernetes", "Series B") | Misses if not in training data |
| Semantic similarity | None ("distributed systems" won't match "microservices") | Excellent |
| Negation | Supported (`-intern`) | Not supported |
| Phrase matching | Supported (`"product manager"`) | Approximate at best |
| Scoring | Lexeme frequency + proximity | Embedding distance |

Empirically, hybrid search with RRF fusion achieves ~84% retrieval precision vs
~62% for pure vector search (per ParadeDB benchmarks).

### Reciprocal Rank Fusion (RRF)

RRF merges two ranked lists by scoring each item as:

```
rrf_score = 1 / (k + rank)
```

Where `k` is a smoothing constant (typically 50-60). Items that appear in both
lists get their scores summed.

Properties:
- Rank-based, not score-based -- avoids the problem of incomparable score scales
- Items ranked #1 in both lists dominate
- Items in only one list still contribute
- `k` controls how sharply top ranks are favored (smaller k = sharper)

### RRF Helper Function

```sql
CREATE OR REPLACE FUNCTION rrf_score(rank int, rrf_k int DEFAULT 50)
RETURNS numeric
LANGUAGE SQL IMMUTABLE PARALLEL SAFE
AS $$
    SELECT COALESCE(1.0 / ($1 + $2), 0.0);
$$;
```

### Complete Hybrid Search Pattern

This pattern runs FTS and vector search as separate subqueries, unions the
ranked results, and merges with RRF:

```sql
WITH fts AS (
    SELECT
        j.id,
        row_number() OVER (
            ORDER BY ts_rank_cd(
                to_tsvector('english', coalesce(j.title, ''))
                || to_tsvector('english', coalesce(j.description_stripped, '')),
                websearch_to_tsquery('english', $1),
                32
            ) DESC
        ) AS rank_ix
    FROM jobs j
    WHERE to_tsvector('english', coalesce(j.title, ''))
       || to_tsvector('english', coalesce(j.description_stripped, ''))
       @@ websearch_to_tsquery('english', $1)
      AND j.listing_status = 'active'
    ORDER BY rank_ix
    LIMIT 60
),
semantic AS (
    SELECT
        e.job_id AS id,
        row_number() OVER (ORDER BY e.embedding <=> $2::vector) AS rank_ix
    FROM job_embeddings e
    JOIN jobs j ON e.job_id = j.id
    WHERE j.listing_status = 'active'
    ORDER BY rank_ix
    LIMIT 60
)
SELECT
    j.*,
    coalesce(1.0 / (50 + fts.rank_ix), 0.0)
  + coalesce(1.0 / (50 + sem.rank_ix), 0.0) AS rrf_score
FROM fts
FULL OUTER JOIN semantic sem ON fts.id = sem.id
JOIN jobs j ON coalesce(fts.id, sem.id) = j.id
ORDER BY rrf_score DESC
LIMIT 25;

-- $1 = user's text query
-- $2 = embedding vector of user's query
```

### Weighted RRF

If FTS and semantic search should not contribute equally:

```sql
-- Weight FTS lower (0.4) and semantic higher (0.6)
ORDER BY
    coalesce(1.0 / (50 + fts.rank_ix), 0.0) * 0.4
  + coalesce(1.0 / (50 + sem.rank_ix), 0.0) * 0.6
  DESC
```

### Integration with Existing Codebase

The current `search_similar_filtered` method in `store.py` uses vector search
with post-hoc FTS filtering (the `title` parameter adds a `@@` WHERE clause).
To upgrade to true hybrid search:

1. Run FTS and vector search as parallel CTEs (as above)
2. Merge with RRF
3. Apply the existing diversity round-robin on top of the RRF-scored results
4. The relevance floor (`DIVERSITY_FLOOR_MULTIPLIER`) would apply to RRF score
   instead of raw distance

If using a stored `fts_vector` column with weights, the FTS CTE simplifies:

```sql
fts AS (
    SELECT j.id,
           row_number() OVER (
               ORDER BY ts_rank_cd(j.fts_vector,
                   websearch_to_tsquery('english', $1), 32) DESC
           ) AS rank_ix
    FROM jobs j
    WHERE j.fts_vector @@ websearch_to_tsquery('english', $1)
      AND j.listing_status = 'active'
    LIMIT 60
)
```

---

## 7. Performance

### EXPLAIN ANALYZE Patterns

GIN queries always produce Bitmap Index Scan -> Bitmap Heap Scan plans:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM jobs
WHERE to_tsvector('english', coalesce(title, ''))
   @@ websearch_to_tsquery('english', 'software engineer')
LIMIT 25;
```

Expected plan:

```
Limit
  -> Bitmap Heap Scan on jobs
       Recheck Cond: (to_tsvector('english', ...) @@ '''softwar'' & ''engin'''::tsquery)
       Heap Blocks: exact=N
       -> Bitmap Index Scan on idx_jobs_title_fts
             Index Cond: (to_tsvector('english', ...) @@ '''softwar'' & ''engin'''::tsquery)
```

**What to look for:**
- `Bitmap Index Scan` -- the GIN index is being used
- `Heap Blocks: exact=N` -- low N means the index is selective
- `Heap Blocks: lossy=N` -- PostgreSQL ran out of `work_mem` for the bitmap and
  fell back to lossy page-level tracking (increase `work_mem` if this happens)
- No `Seq Scan` -- if you see this, the planner is not using the index (check
  that the expression in the query matches the index expression exactly)

### Expression Match Requirement

The query expression must **exactly match** the index expression. This is the
most common source of "index not used":

```sql
-- Index is on: to_tsvector('english', coalesce(title, ''))
-- This WILL use the index:
WHERE to_tsvector('english', coalesce(title, '')) @@ query
-- This WILL NOT use the index (missing coalesce):
WHERE to_tsvector('english', title) @@ query
-- This WILL NOT use the index (wrong config):
WHERE to_tsvector('simple', coalesce(title, '')) @@ query
```

A stored tsvector column avoids this problem entirely -- the index is on the
column, and any `@@` query against that column uses it.

### Common Pitfalls

1. **Config mismatch between index and query.** The `to_tsvector` call in the
   query must use the same config as the one in the index expression. If you
   omit the config, it uses `default_text_search_config`, which may differ.

2. **OR queries can be slow.** `'python' | 'golang' | 'rust' | 'java'` forces
   the GIN index to scan and merge multiple posting lists. For very broad OR
   queries, consider running separate queries and merging in application code.

3. **Ranking without LIMIT.** `ts_rank` and `ts_rank_cd` are evaluated per row.
   Always filter with `@@` first (which uses the index), then rank only the
   matching rows. Never rank all rows and filter by score.

4. **Stale statistics.** After bulk loading (like `jsb sync`), run `ANALYZE jobs`
   so the planner has accurate statistics for cost estimation.

5. **GIN pending list on bulk loads.** After `jsb sync`, the pending list may be
   large. Either let autovacuum handle it or run
   `SELECT gin_clean_pending_list('idx_jobs_title_fts')` explicitly.

### Useful Diagnostic Queries

```sql
-- Check index size
SELECT pg_size_pretty(pg_relation_size('idx_jobs_title_fts')) AS index_size;

-- Check which text search config is the default
SHOW default_text_search_config;

-- See how a string is tokenized and which dictionaries match
SELECT * FROM ts_debug('english', 'Senior DevOps Engineer - Remote');

-- See the lexemes and positions in a tsvector
SELECT to_tsvector('english', 'Senior Software Engineer at Cloudflare');

-- Count lexemes in a document's tsvector
SELECT array_length(
    string_to_array(
        to_tsvector('english', description_stripped)::text, ' '
    ), 1
) AS lexeme_count
FROM jobs WHERE id = 123;
```

---

## 8. PostgreSQL 18 Specifics for Full-Text Search

PostgreSQL 18 (released September 2025) does not introduce major new FTS
features, but has several changes that affect FTS behavior:

### Collation Provider Change

FTS now uses the **default collation provider of the cluster** to read
configuration files and dictionaries, rather than always using libc. If the
cluster uses ICU or builtin as the default provider, LC_CTYPE-sensitive
operations (like stop word matching) may behave differently.

**Action required after pg_upgrade to 18:** Reindex all FTS and pg_trgm indexes.

```sql
REINDEX INDEX CONCURRENTLY idx_jobs_title_fts;
REINDEX INDEX CONCURRENTLY idx_jobs_desc_fts;
```

### Virtual Generated Columns (Default)

`GENERATED ALWAYS AS (expr)` now defaults to `VIRTUAL` instead of `STORED`.
Virtual columns compute on read and use no disk space. However, **GIN indexes
require STORED generated columns** -- you must explicitly specify `STORED` when
creating a tsvector generated column:

```sql
-- PG 18: must explicitly say STORED for GIN-indexable columns
ALTER TABLE jobs ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(description_stripped, '')), 'B')
    ) STORED;  -- STORED is required, VIRTUAL won't work with GIN
```

### Estonian Stemming

PG 18 adds Estonian as a supported Snowball stemmer language. Not relevant for
English job search, but worth noting for multilingual search applications.

### casefold() Function

New `casefold()` function provides Unicode-aware case folding, useful for
case-insensitive matching that handles characters with multiple case variants.
Not directly related to FTS (which handles case internally) but useful for
supplementary ILIKE filters.

### Nondeterministic Collation Support

LIKE and text position functions now work with nondeterministic collations.
This enables accent-insensitive or case-insensitive LIKE queries without
custom workarounds -- useful for location filtering where "San Jose" should
match "San Jos&eacute;".

### Async I/O Subsystem

The new async I/O subsystem can improve GIN index scan performance by up to 3x
for cold-cache reads. The GIN Bitmap Heap Scan pattern (which fetches many
scattered heap pages) benefits significantly from read-ahead I/O.

---

## Appendix: Quick Reference for jobsearch-buddy

### Current FTS State

- Migration `008_fts_indexes.sql` creates per-field GIN expression indexes on
  `title` and `description_stripped`
- `store.py` line 508 uses `websearch_to_tsquery('english', ...)` with `@@` for
  the `title` filter parameter
- No stored tsvector column; no weighting; no FTS ranking (just boolean match)

### Upgrade Path for Weighted Hybrid Search

1. Add a `STORED` generated `fts_vector` column with `setweight` (A=title,
   B=description_stripped, C=location, D=department)
2. Create a single GIN index on `fts_vector` (replaces the two expression indexes)
3. Replace the boolean `@@` filter with a ranked FTS CTE
4. Merge FTS and vector results with RRF
5. Apply diversity round-robin on the merged results

### Key SQL Patterns

```sql
-- Boolean FTS match (current)
WHERE to_tsvector('english', coalesce(j.title, ''))
   @@ websearch_to_tsquery('english', 'software engineer')

-- Ranked FTS (upgrade target)
SELECT id, ts_rank_cd(fts_vector, websearch_to_tsquery('english', $1), 32) AS score
FROM jobs
WHERE fts_vector @@ websearch_to_tsquery('english', $1)
ORDER BY score DESC
LIMIT 60

-- Hybrid RRF (full upgrade)
-- See Section 6 for complete pattern
```
