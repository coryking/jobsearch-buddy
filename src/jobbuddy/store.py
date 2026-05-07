"""JobStore — PostgreSQL persistence layer for job listings.

Schema uses a surrogate SERIAL PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id).
"""

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, LiteralString
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Json

from jobbuddy.models import Account, Company, Job
from jobbuddy.types import DistillWorkItem, ResearchWorkItem

log = logging.getLogger(__name__)


def _utcnow() -> str:
    """ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _validate_date(value: str | date | None) -> date | None:
    """Coerce to date. Returns None for malformed values."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class JobStore:
    """PostgreSQL persistence for job listings, sync bookkeeping, and activity log.

    Args:
        conninfo: psycopg connection string, or None for default from settings.
    """

    def __init__(self, conninfo: str | None = None):
        self.conn = self._connect(conninfo)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -------------------------------------------------------------------
    # Connection + Schema
    # -------------------------------------------------------------------

    def _connect(self, conninfo: str | None) -> psycopg.Connection[DictRow]:
        if conninfo is None:
            from jobbuddy.settings import pg_conninfo_with_token
            conninfo = pg_conninfo_with_token()

        return psycopg.Connection[DictRow].connect(
            conninfo, autocommit=True, row_factory=dict_row,
        )

    # -------------------------------------------------------------------
    # Companies
    # -------------------------------------------------------------------

    def _hydrate_company(self, row: dict) -> Company:
        """Build a Company from a DB row."""
        config = row["config"] or {}
        return Company(
            slug=row["slug"], name=row["name"],
            ats=row["ats"], board=row["board"],
            short_bio=row.get("short_bio"),
            long_bio=row.get("long_bio"),
            **config,
        )

    def load_companies(self) -> dict[str, Company]:
        """Load all companies. Returns {slug: Company}."""
        rows = self.conn.execute(
            "SELECT * FROM companies ORDER BY slug"
        ).fetchall()
        return {r["slug"]: self._hydrate_company(r) for r in rows}

    def get_company(self, slug: str) -> Company | None:
        """Get a single company by slug."""
        row = self.conn.execute(
            "SELECT * FROM companies WHERE slug = %s", (slug,)
        ).fetchone()
        return self._hydrate_company(row) if row else None

    def save_company(self, company: Company) -> None:
        """Upsert a company. COALESCE on ats/board so ensure_company(ats=None)
        doesn't clobber existing ATS config. Bios are owned by the research
        phase and are never touched here."""
        extra = {
            k: v for k, v in company.model_dump().items()
            if k not in ("slug", "name", "ats", "board", "short_bio", "long_bio")
        }
        self.conn.execute(
            """INSERT INTO companies (slug, name, ats, board, config)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (slug) DO UPDATE SET
                name = excluded.name,
                ats = COALESCE(excluded.ats, companies.ats),
                board = COALESCE(excluded.board, companies.board),
                config = CASE
                    WHEN excluded.ats IS NOT NULL THEN excluded.config
                    ELSE companies.config
                END""",
            (company.slug, company.name, company.ats, company.board, Json(extra)),
        )

    def count_companies_needing_bio(self, *, slugs: list[str] | None = None) -> int:
        """Count companies with no researched long_bio. Optional slug filter."""
        row = self.conn.execute(
            """SELECT COUNT(*) AS cnt FROM companies
               WHERE long_bio IS NULL
                 AND (%s::text[] IS NULL OR slug = ANY(%s::text[]))""",
            (slugs, slugs),
        ).fetchone()
        assert row is not None  # COUNT(*) always returns one row
        return row["cnt"]

    def get_companies_needing_bio(
        self, limit: int = 50, *, slugs: list[str] | None = None,
    ) -> list[ResearchWorkItem]:
        """Return companies with no researched long_bio, slug + name only."""
        rows = self.conn.execute(
            """SELECT slug, name FROM companies
               WHERE long_bio IS NULL
                 AND (%s::text[] IS NULL OR slug = ANY(%s::text[]))
               ORDER BY slug
               LIMIT %s""",
            (slugs, slugs, limit),
        ).fetchall()
        return [{"slug": r["slug"], "name": r["name"]} for r in rows]

    def update_company_bio(
        self, slug: str, *, short_bio: str, long_bio: str, model: str,
    ) -> None:
        """Persist a researched bio. bio_researched_at = now()."""
        self.conn.execute(
            """UPDATE companies SET
                short_bio = %s,
                long_bio = %s,
                bio_model = %s,
                bio_researched_at = now()
               WHERE slug = %s""",
            (short_bio, long_bio, model, slug),
        )

    def clear_company_bios(self, *, slugs: list[str] | None = None) -> int:
        """Clear researched bios. Returns row count touched.

        With slugs=None, clears every company. The CLI guards against
        accidental global clears; callers in code should pass slugs."""
        result = self.conn.execute(
            """UPDATE companies SET
                short_bio = NULL,
                long_bio = NULL,
                bio_researched_at = NULL,
                bio_model = NULL
               WHERE %s::text[] IS NULL OR slug = ANY(%s::text[])""",
            (slugs, slugs),
        )
        return result.rowcount

    # -------------------------------------------------------------------
    # Company bio embeddings (Phase 2: find_companies)
    # -------------------------------------------------------------------

    def update_company_embedding(self, slug: str, *, embedding: str) -> None:
        """Persist a bio embedding. `embedding` is the pgvector text literal
        `'[v1,v2,...]'` produced by jobbuddy.embeddings.embed_text()."""
        self.conn.execute(
            """UPDATE companies SET
                bio_embedding = %s::vector,
                bio_embedding_updated_at = now()
               WHERE slug = %s""",
            (embedding, slug),
        )

    # Reciprocal Rank Fusion constant. k=60 is the canonical default from
    # Cormack et al. (2009); larger k softens the weight of top ranks.
    _RRF_K = 60

    def find_companies(
        self, embedding: str, query: str, *, limit: int = 20,
    ) -> list[dict]:
        """Hybrid company search: vector ∪ FTS, fused via Reciprocal Rank Fusion.

        Vector arm: cosine similarity over `bio_embedding` (handles vibe
        queries — "AI-as-product startups").
        FTS arm: websearch_to_tsquery over `name + short_bio` (handles
        exact-name lookups — "Stripe", "Mirabel AI" — that vector alone
        scores poorly because short queries have weak semantic signal).
        Fusion: RRF with k=60. Each arm fetches LIMIT*3 candidates so the
        merge has room to surface rows ranked highly by only one arm.

        Returns rows with `slug`, `name`, `short_bio`, `active_jobs` (count
        of jobs with `listing_status = 'active'`), plus three internal scores:
        - `vec_score`: cosine similarity in [-1, 1], or NULL if vector arm
          missed this row
        - `fts_score`: ts_rank, or NULL if FTS arm missed this row
        - `rrf_score`: fused rank score (sums to ~0.03 max with k=60)

        The scores are used by `jsb search-debug` for tuning; `core.find_companies`
        strips them before returning to MCP/CLI callers.

        At 693 companies the FTS arm runs as a sequential scan with inline
        to_tsvector — no GIN index needed. Add the index when row count
        grows past where seq scan stays fast.
        """
        candidate_pool = max(limit * 3, 30)
        rows = self.conn.execute(
            """
            WITH vec AS (
                SELECT slug,
                       1 - (bio_embedding <=> %(embedding)s::vector) AS vec_score,
                       ROW_NUMBER() OVER (
                           ORDER BY bio_embedding <=> %(embedding)s::vector
                       ) AS vec_rank
                  FROM companies
                 WHERE bio_embedding IS NOT NULL
                 ORDER BY bio_embedding <=> %(embedding)s::vector
                 LIMIT %(pool)s
            ),
            fts AS (
                SELECT slug,
                       ts_rank(
                           to_tsvector('english', coalesce(name, '') || ' ' || coalesce(short_bio, '')),
                           websearch_to_tsquery('english', %(query)s)
                       ) AS fts_score,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(
                               to_tsvector('english', coalesce(name, '') || ' ' || coalesce(short_bio, '')),
                               websearch_to_tsquery('english', %(query)s)
                           ) DESC
                       ) AS fts_rank
                  FROM companies
                 WHERE to_tsvector('english', coalesce(name, '') || ' ' || coalesce(short_bio, ''))
                       @@ websearch_to_tsquery('english', %(query)s)
                 LIMIT %(pool)s
            ),
            fused AS (
                SELECT COALESCE(v.slug, f.slug) AS slug,
                       v.vec_score, f.fts_score,
                       COALESCE(1.0 / (%(k)s + v.vec_rank), 0)
                       + COALESCE(1.0 / (%(k)s + f.fts_rank), 0) AS rrf_score
                  FROM vec v
                  FULL OUTER JOIN fts f ON v.slug = f.slug
            )
            SELECT c.slug, c.name, c.short_bio,
                   fused.vec_score, fused.fts_score, fused.rrf_score,
                   COALESCE(j.active_jobs, 0) AS active_jobs
              FROM fused
              JOIN companies c ON c.slug = fused.slug
              LEFT JOIN (
                  SELECT company_slug, COUNT(*) AS active_jobs
                    FROM jobs
                   WHERE listing_status = 'active'
                   GROUP BY company_slug
              ) j ON j.company_slug = c.slug
             ORDER BY fused.rrf_score DESC
             LIMIT %(limit)s
            """,
            {
                "embedding": embedding,
                "query": query,
                "pool": candidate_pool,
                "k": self._RRF_K,
                "limit": limit,
            },
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Jobs
    # -------------------------------------------------------------------

    def upsert_jobs(self, slug: str, jobs: list[Job]) -> None:
        """Sync jobs for a company: upsert current, soft-delete stale."""
        now = _utcnow()

        # Deduplicate by job_id (last wins)
        by_id: dict[str, Job] = {}
        for j in jobs:
            by_id[j.id] = j
        jobs = list(by_id.values())

        with self.conn.transaction():
            current_ids = {
                row["job_id"]
                for row in self.conn.execute(
                    "SELECT job_id FROM jobs WHERE company_slug = %s", (slug,)
                ).fetchall()
            }
            new_ids = {j.id for j in jobs}

            # Detect re-posts: removed jobs about to be reactivated
            returning_ids = new_ids & current_ids
            if returning_ids:
                reposts = self.conn.execute(
                    """SELECT job_id, title FROM jobs
                       WHERE company_slug = %s AND job_id = ANY(%s)
                         AND listing_status = 'removed'""",
                    (slug, list(returning_ids)),
                ).fetchall()
                for r in reposts:
                    log.info("Repost detected: %s job_id=%s (%s)", slug, r["job_id"], r["title"])

            gone = current_ids - new_ids
            if gone:
                with self.conn.cursor() as cur:
                    cur.executemany(
                        """UPDATE jobs SET listing_status = 'removed'
                           WHERE company_slug = %s AND job_id = %s AND listing_status = 'active'""",
                        [(slug, jid) for jid in gone],
                        returning=False,
                    )

            upsert_params = [
                (
                    slug,
                    j.id,
                    j.title,
                    j.location or None,
                    j.url or None,
                    _validate_date(j.published_at),
                    j.department or None,
                    j.team or None,
                    j.salary or None,
                    j.description or None,
                    json.dumps(j.ats_metadata) if j.ats_metadata else None,
                    now,
                )
                for j in jobs
            ]
            with self.conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO jobs
                       (company_slug, job_id, title, location, url, published_at,
                        department, team, salary, description, ats_metadata, last_seen,
                        listing_status)
                       VALUES (%s, %s, %s, %s, %s,
                               COALESCE(%s, CURRENT_DATE),
                               %s, %s, %s, %s, %s, %s, 'active')
                       ON CONFLICT(company_slug, job_id) DO UPDATE SET
                        -- Pure-insert model: a row's content (title, location,
                        -- description, salary, dates, etc.) is fixed at first
                        -- insert. Only operational state mutates here:
                        --   last_seen      -- bumped every sync
                        --   listing_status -- back to 'active' if we'd marked it removed
                        -- The manage_removed_at trigger clears removed_at on
                        -- the listing_status flip back to 'active'. Filling
                        -- NULLs the fetcher couldn't produce (descriptions on
                        -- stub fetchers, posted dates on TalentBrew/Avature)
                        -- is the enrich phase's job, via update_enrichment.
                        last_seen = excluded.last_seen,
                        listing_status = 'active'
                       WHERE jobs.last_seen IS DISTINCT FROM excluded.last_seen
                          OR jobs.listing_status <> 'active'""",
                    upsert_params,
                    returning=False,
                )

            self.conn.execute(
                """INSERT INTO sync_status
                   (company_slug, last_sync, job_count, error)
                   VALUES (%s, %s, %s, NULL)
                   ON CONFLICT(company_slug) DO UPDATE SET
                    last_sync = excluded.last_sync,
                    job_count = excluded.job_count,
                    error = NULL""",
                (slug, now, len(jobs)),
            )

    def query_jobs(
        self,
        *,
        companies: list[str] | None = None,
        exclude_companies: list[str] | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        include_removed: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Query jobs with keyword filters and round-robin diversity.

        When searching across multiple companies, fetches a pool then
        round-robins across companies by recency so no single employer
        dominates the result set. Single-company queries skip diversity
        (no need to round-robin with one company).
        """
        conditions, params = self._build_filter_conditions(
            companies=companies, exclude_companies=exclude_companies,
            title=title, location=location, posted_after=posted_after,
            include_removed=include_removed,
        )

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if companies and len(companies) == 1:
            params.append(limit)
            sql = f"""
                SELECT j.*, s.last_sync, c.name AS company_name
                FROM jobs j
                LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                LEFT JOIN companies c ON j.company_slug = c.slug
                {where}
                ORDER BY j.published_at DESC NULLS LAST
                LIMIT %s
            """
        else:
            pool_size = max(self.DIVERSITY_POOL_SIZE, limit * 5)
            params.append(pool_size)
            sql = f"""
                WITH base AS (
                    SELECT j.*, s.last_sync, c.name AS company_name
                    FROM jobs j
                    LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                    LEFT JOIN companies c ON j.company_slug = c.slug
                    {where}
                    ORDER BY j.published_at DESC NULLS LAST
                    LIMIT %s
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY company_slug
                               ORDER BY published_at DESC NULLS LAST
                           ) AS rn
                    FROM base
                )
                SELECT * FROM ranked
                ORDER BY rn, published_at DESC NULLS LAST
                LIMIT %s
            """
            params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [{k: v for k, v in dict(row).items() if k != "rn"} for row in rows]

    PER_COMPANY_CAP_DEFAULT = 3

    def search_jobs_fts(
        self,
        *,
        query: str | None = None,
        companies: list[str] | None = None,
        exclude_companies: list[str] | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        include_removed: bool = False,
        limit: int = 20,
        per_company_cap: int | None = PER_COMPANY_CAP_DEFAULT,
    ) -> list[dict]:
        """Phase 1 search: FTS over fts_vector with deterministic ranking.

        - When `query` is set: rank by ts_rank DESC, tie-break on
          (published_at DESC NULLS LAST, company_slug, job_id).
        - When `query` is empty: pure published_at DESC NULLS LAST,
          tie-break on (company_slug, job_id).
        - Per-company cap: keeps the top N rows per company in the result
          set so a single dominant employer (Carvana auto-body, Anduril
          robotics) doesn't crowd cross-employer evidence out of the
          fixed-size top-K. Skipped when the caller already scoped to a
          single company or passes None.
        - Returns rows including short_jd, salary, published_at — the
          fact-dense shape the calling LLM filters on.
        """
        conditions, params = self._build_filter_conditions(
            companies=companies, exclude_companies=exclude_companies,
            title=query, location=location, posted_after=posted_after,
            include_removed=include_removed,
        )

        # Three ORDER BY contexts:
        #  - `order_window`: inside row_number() OVER. Cannot use the `rank`
        #    alias (window evaluation precedes SELECT-list aliasing in PG).
        #    Must repeat the full ts_rank() expression and use `j.` prefixes.
        #  - `order_inner`: top-level ORDER BY of the cap-less query. Can use
        #    the `rank` alias. Uses `j.` prefixes for unambiguous join columns.
        #  - `order_outer`: ORDER BY over the CTE result in the cap path.
        #    Uses unqualified column names since CTE columns are flat.
        ts_rank_expr = "ts_rank(j.fts_vector, websearch_to_tsquery('english', %s))"
        select_extra = ""
        order_window = "j.published_at DESC NULLS LAST, j.company_slug, j.job_id"
        order_inner = order_window
        order_outer = "published_at DESC NULLS LAST, company_slug, job_id"
        if query:
            select_extra = f", {ts_rank_expr} AS rank"
            params.insert(0, query)  # bound to the SELECT-list ts_rank
            order_window = f"{ts_rank_expr} DESC, " + order_window
            order_inner = "rank DESC, " + order_inner
            order_outer = "rank DESC, " + order_outer

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Skip the cap when the caller explicitly scoped to one company —
        # the user wants depth there, not breadth.
        apply_cap = (
            per_company_cap is not None
            and per_company_cap > 0
            and not (companies and len(companies) == 1)
        )

        if apply_cap:
            sql = f"""
                WITH ranked AS (
                    SELECT j.*, s.last_sync, c.name AS company_name{select_extra},
                           row_number() OVER (
                               PARTITION BY j.company_slug
                               ORDER BY {order_window}
                           ) AS company_rn
                      FROM jobs j
                      LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                      LEFT JOIN companies c ON j.company_slug = c.slug
                      {where}
                )
                SELECT * FROM ranked
                 WHERE company_rn <= %s
                 ORDER BY {order_outer}
                 LIMIT %s
            """
            # When `query` is set, the OVER clause's ts_rank expression needs
            # its own bound copy of the query (right after the SELECT-list one
            # we already inserted, before the WHERE-clause params).
            if query:
                params.insert(1, query)
            params.extend([per_company_cap, limit])
        else:
            sql = f"""
                SELECT j.*, s.last_sync, c.name AS company_name{select_extra}
                  FROM jobs j
                  LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                  LEFT JOIN companies c ON j.company_slug = c.slug
                  {where}
                  ORDER BY {order_inner}
                  LIMIT %s
            """
            params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [{k: v for k, v in dict(r).items() if k != "company_rn"} for r in rows]

    # Columns the enrich phase is allowed to ask about. Whitelisted to keep
    # the SQL composer safe — the column tuple comes from a fetcher class
    # attribute, but defense-in-depth costs nothing here.
    _ENRICHMENT_COLUMNS: frozenset[LiteralString] = frozenset({"description", "published_at", "salary"})

    def get_jobs_needing_enrichment(
        self, slug: str, columns: tuple[str, ...]
    ) -> list[dict]:
        """Return active jobs in `slug` where ANY of `columns` is NULL.

        Generalizes get_jobs_needing_descriptions so a fetcher can declare
        multiple detail-page-derivable fields (description, published_at,
        salary) via ATSFetcher.enrichment_fills, and the enrich phase
        re-fetches a row whenever any one of them is missing.
        """
        if not columns:
            raise ValueError("get_jobs_needing_enrichment: columns must be non-empty")
        bad = set(columns) - self._ENRICHMENT_COLUMNS
        if bad:
            raise ValueError(
                f"get_jobs_needing_enrichment: unsupported columns {sorted(bad)}; "
                f"allowed: {sorted(self._ENRICHMENT_COLUMNS)}"
            )
        # Iterate the LiteralString whitelist (not the str-typed `columns`
        # input) so the resulting clause is LiteralString.
        null_clause = " OR ".join(
            f"{c} IS NULL" for c in self._ENRICHMENT_COLUMNS if c in columns
        )
        sql = (
            "SELECT job_id, title, ats_metadata FROM jobs "
            f"WHERE company_slug = %s AND listing_status = 'active' AND ({null_clause})"
        )
        rows = self.conn.execute(sql, (slug,)).fetchall()
        return [dict(row) for row in rows]

    def get_jobs_needing_descriptions(self, slug: str) -> list[dict]:
        """Back-compat shim around get_jobs_needing_enrichment."""
        return self.get_jobs_needing_enrichment(slug, ("description",))

    def update_descriptions(self, slug: str, descs: dict[str, str]) -> None:
        """Batch update description column for jobs in a company."""
        if not descs:
            return

        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.executemany(
                    "UPDATE jobs SET description = %s WHERE company_slug = %s AND job_id = %s",
                    [(desc, slug, job_id) for job_id, desc in descs.items()],
                    returning=False,
                )

    # Per-column write expression for update_enrichment. All columns use
    # COALESCE(jobs.X, %s) — "old wins". This matches the pure-insert
    # contract: a row's content is fixed at first non-NULL value. Enrich
    # fills NULLs; it never overwrites populated data. A row whose OR
    # predicate (description IS NULL OR published_at IS NULL) matches
    # because of one missing column won't have its other already-populated
    # columns rewritten with re-parsed values.
    _ENRICHMENT_WRITE_EXPR: dict[LiteralString, LiteralString] = {
        "description": "COALESCE(jobs.description, %s)",
        "salary": "COALESCE(jobs.salary, %s)",
        "published_at": "COALESCE(jobs.published_at, %s)",
    }

    def update_enrichment(self, slug: str, payloads: dict[str, dict]) -> None:
        """Write per-column enrichment results for a company.

        `payloads` is `{job_id: {column: value, ...}}`; only keys in
        `_ENRICHMENT_COLUMNS` are accepted. Each per-job payload may carry
        a different subset of columns. Empty payloads are skipped. Bad
        column names raise before any write, so a bad payload doesn't
        half-write the batch.
        """
        if not payloads:
            return

        for jid, fields in payloads.items():
            bad = set(fields) - self._ENRICHMENT_COLUMNS
            if bad:
                raise ValueError(
                    f"update_enrichment: unsupported columns {sorted(bad)} "
                    f"for job {jid}; allowed: {sorted(self._ENRICHMENT_COLUMNS)}"
                )

        with self.conn.transaction():
            with self.conn.cursor() as cur:
                for jid, fields in payloads.items():
                    if not fields:
                        continue
                    # Iterate the LiteralString whitelist (not str-typed
                    # `fields`) so cols is list[LiteralString] and the
                    # composed UPDATE stays LiteralString.
                    cols: list[LiteralString] = sorted(c for c in self._ENRICHMENT_COLUMNS if c in fields)
                    set_clause = ", ".join(
                        f"{c} = {self._ENRICHMENT_WRITE_EXPR[c]}" for c in cols
                    )
                    params = tuple(fields[c] for c in cols) + (slug, jid)
                    cur.execute(
                        f"UPDATE jobs SET {set_clause} "
                        f"WHERE company_slug = %s AND job_id = %s",
                        params,
                    )

    def mark_listing_removed(self, slug: str, job_id: str) -> None:
        """Flip listing_status to 'removed' for a single job.

        Used by the enrich phase when an ATS detail-page request 404s —
        the listing has been pulled at the source. The manage_removed_at
        trigger sets removed_at. No-op if already removed.
        """
        self.conn.execute(
            "UPDATE jobs SET listing_status = 'removed' "
            "WHERE company_slug = %s AND job_id = %s AND listing_status = 'active'",
            (slug, job_id),
        )

    # -------------------------------------------------------------------
    # Distill phase
    # -------------------------------------------------------------------

    def _distill_conditions(self, slugs: list[str] | None) -> tuple[LiteralString, list]:
        """Stable column-presence predicate matching idx_jobs_needs_distill."""
        conditions: list[LiteralString] = [
            "description IS NOT NULL",
            "short_jd IS NULL",
            "listing_status = 'active'",
        ]
        params: list = []
        if slugs:
            conditions.append("company_slug = ANY(%s)")
            params.append(slugs)
        return " AND ".join(conditions), params

    def count_jobs_needing_distill(self, *, slugs: list[str] | None = None) -> int:
        """Count active jobs with a description but no distilled short_jd."""
        where, params = self._distill_conditions(slugs)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM jobs WHERE {where}", params
        ).fetchone()
        assert row is not None  # COUNT(*) always returns one row
        return row["cnt"]

    def get_jobs_needing_distill(
        self, limit: int = 50, *, slugs: list[str] | None = None,
    ) -> list[DistillWorkItem]:
        """Return jobs needing distill, joined with company name for the prompt.

        ORDER BY (company_slug, location, id) groups same-company jobs
        adjacent so the system_prompt + company_bio prefix stays warm in
        Azure's prompt cache across calls — cuts input cost by ~15% on
        the 90% of jobs at companies with 100+ active listings. The
        compound key remains deterministic to avoid the concurrent-poll
        race documented in the deleted sync/embed.py.
        """
        where, params = self._distill_conditions(slugs)
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT j.id, j.company_slug, c.name AS company_name,
                       j.job_id, j.title, j.location, j.salary, j.description
                  FROM jobs j
                  LEFT JOIN companies c ON c.slug = j.company_slug
                 WHERE {where}
                 ORDER BY j.company_slug, j.location, j.id
                 LIMIT %s""",
            params,
        ).fetchall()
        return [
            {
                "id": r["id"],
                "company_slug": r["company_slug"],
                "company_name": r["company_name"] or r["company_slug"],
                "job_id": r["job_id"],
                "title": r["title"],
                "location": r["location"],
                "salary": r["salary"],
                "description": r["description"],
            }
            for r in rows
        ]

    def update_job_distill(
        self, job_pk: int, *,
        short_jd: str,
        description_normalized: str,
        salary: str | None,
    ) -> None:
        """Persist distill outputs for one job. salary may be None.

        salary is only overwritten when distill produced a value — the
        existing structured-field salary (set by the fetcher) is preserved
        when distill returns None.
        """
        self.conn.execute(
            """UPDATE jobs SET
                short_jd = %s,
                description_normalized = %s,
                salary = COALESCE(%s, salary)
               WHERE id = %s""",
            (short_jd, description_normalized, salary, job_pk),
        )

    def get_job_by_ids(self, job_ids: list[int]) -> list[dict]:
        """Fetch jobs by surrogate key IDs."""
        if not job_ids:
            return []
        rows = self.conn.execute(
            """SELECT j.*, s.last_sync, c.name AS company_name
                FROM jobs j
                LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                LEFT JOIN companies c ON j.company_slug = c.slug
                WHERE j.id = ANY(%s)""",
            (job_ids,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_jobs_by_external_ids(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Fetch jobs by (company_slug, job_id) pairs. Returns {(slug, job_id): row}."""
        if not pairs:
            return {}
        slugs = [s for s, _ in pairs]
        jids = [j for _, j in pairs]
        rows = self.conn.execute(
            """SELECT j.*, s.last_sync, c.name AS company_name
               FROM jobs j
               LEFT JOIN sync_status s ON j.company_slug = s.company_slug
               LEFT JOIN companies c ON j.company_slug = c.slug
               WHERE (j.company_slug, j.job_id) IN (
                   SELECT unnest(%s::text[]), unnest(%s::text[])
               )""",
            (slugs, jids),
        ).fetchall()
        return {(r["company_slug"], r["job_id"]): dict(r) for r in rows}

    def _build_filter_conditions(
        self,
        *,
        companies: list[str] | None = None,
        exclude_companies: list[str] | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        include_removed: bool = False,
    ) -> tuple[list[LiteralString], list]:
        """Build WHERE conditions and params for job search filters."""
        conditions: list[LiteralString] = []
        params: list = []

        if not include_removed:
            conditions.append("j.listing_status = 'active'")

        if companies:
            conditions.append("j.company_slug = ANY(%s)")
            params.append(companies)

        if exclude_companies:
            conditions.append("NOT (j.company_slug = ANY(%s))")
            params.append(exclude_companies)

        if posted_after:
            conditions.append("j.published_at >= %s")
            params.append(posted_after)

        if title:
            conditions.append("j.fts_vector @@ websearch_to_tsquery('english', %s)")
            params.append(title)

        if location:
            terms = [t.strip() for t in location.split(",") if t.strip()]
            if terms:
                or_clauses: list[LiteralString] = ["j.location ILIKE %s"] * len(terms)
                conditions.append("(" + " OR ".join(or_clauses) + ")")
                params.extend(f"%{t}%" for t in terms)

        return conditions, params

    DIVERSITY_POOL_SIZE = 500

    # -------------------------------------------------------------------
    # Sync bookkeeping
    # -------------------------------------------------------------------

    def is_stale(self, slug: str, hours: float) -> bool:
        """Check if a company needs re-syncing."""
        row = self.conn.execute(
            "SELECT last_sync FROM sync_status WHERE company_slug = %s",
            (slug,),
        ).fetchone()
        if not row:
            return True
        try:
            last = row["last_sync"]
            if isinstance(last, str):
                last = datetime.fromisoformat(last)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            return elapsed > hours
        except (ValueError, TypeError):
            return True

    def record_sync(self, slug: str, count: int) -> None:
        """Record a successful sync."""
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (%s, %s, %s, NULL)
               ON CONFLICT(company_slug) DO UPDATE SET
                last_sync = excluded.last_sync,
                job_count = excluded.job_count,
                error = NULL""",
            (slug, now, count),
        )

    def record_sync_error(self, slug: str, error: str) -> None:
        """Record a sync failure."""
        now = _utcnow()
        row = self.conn.execute(
            "SELECT job_count FROM sync_status WHERE company_slug = %s",
            (slug,),
        ).fetchone()
        job_count = row["job_count"] if row else 0
        self.conn.execute(
            """INSERT INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(company_slug) DO UPDATE SET
                last_sync = excluded.last_sync,
                job_count = excluded.job_count,
                error = excluded.error""",
            (slug, now, job_count, error),
        )

    def get_sync_status(self, slug: str | None = None) -> list[dict]:
        """Get sync status for one or all companies."""
        if slug:
            rows = self.conn.execute(
                "SELECT * FROM sync_status WHERE company_slug = %s", (slug,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM sync_status ORDER BY last_sync DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def job_count(self, include_removed: bool = False) -> int:
        """Total number of jobs in the store."""
        sql = "SELECT COUNT(*) as cnt FROM jobs"
        if not include_removed:
            sql += " WHERE listing_status = 'active'"
        row = self.conn.execute(sql).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------

    def has_any_jobs(self) -> bool:
        """Whether the jobs table has at least one row."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
        return row["cnt"] > 0 if row else False

    # -------------------------------------------------------------------
    # Activity Log
    # -------------------------------------------------------------------

    _ACTIVITY_FIELDS = ["date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"]

    def _activity_row_to_dict(self, row: dict) -> dict:
        """Convert a DB row to the consumer dict contract.

        Keys match FIELDNAMES from the old CSV: date (not log_date),
        and NULL → empty string for optional fields.
        """
        return {
            "date": row["log_date"].isoformat() if row["log_date"] else "",
            "company": row["company"] or "",
            "role": row["role"] or "",
            "job_id": row["job_id"] or "",
            "action": row["action"] or "",
            "person": row["person"] or "",
            "location": row["location"] or "",
            "status": row["status"] or "",
            "url": row["url"] or "",
            "notes": row["notes"] or "",
        }

    def append_activity(
        self,
        account_id: UUID,
        company: str,
        role: str,
        action: str,
        *,
        job_id: str = "",
        person: str = "",
        location: str = "",
        status: str = "",
        url: str = "",
        notes: str = "",
        row_date: str | None = None,
    ) -> dict:
        """Append an activity to the log. Returns the row dict."""
        row = self.conn.execute(
            """INSERT INTO activity_log
               (account_id, log_date, company, role, job_id, action, person, location, status, url, notes)
               VALUES (%s, COALESCE(%s::date, CURRENT_DATE), %s, %s, NULLIF(%s,''), NULLIF(%s,''),
                       NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))
               RETURNING *""",
            (account_id, row_date, company, role, job_id, action, person, location, status, url, notes),
        ).fetchone()
        assert row is not None  # INSERT ... RETURNING * always yields the inserted row
        return self._activity_row_to_dict(row)

    def read_activity_log(self, account_id: UUID) -> list[dict]:
        """Read all activity log rows for one account, most recent first."""
        rows = self.conn.execute(
            "SELECT * FROM activity_log WHERE account_id = %s ORDER BY log_date DESC, id DESC",
            (account_id,),
        ).fetchall()
        return [self._activity_row_to_dict(r) for r in rows]

    def find_activity_duplicates(
        self, account_id: UUID, url: str = "", company: str = "", role: str = ""
    ) -> list[dict]:
        """Find rows for one account matching a URL, or company+role combo."""
        if url:
            rows = self.conn.execute(
                """SELECT * FROM activity_log
                   WHERE account_id = %s AND url = %s
                   ORDER BY log_date DESC, id DESC""",
                (account_id, url.strip()),
            ).fetchall()
        elif company and role:
            rows = self.conn.execute(
                """SELECT * FROM activity_log
                   WHERE account_id = %s AND lower(company) = lower(%s) AND lower(role) = lower(%s)
                   ORDER BY log_date DESC, id DESC""",
                (account_id, company, role),
            ).fetchall()
        else:
            return []
        return [self._activity_row_to_dict(r) for r in rows]

    def find_activity_by_company(self, account_id: UUID, company: str) -> list[dict]:
        """Find one account's activity rows for a company (case-insensitive)."""
        rows = self.conn.execute(
            """SELECT * FROM activity_log
               WHERE account_id = %s AND lower(company) = lower(%s)
               ORDER BY log_date DESC, id DESC""",
            (account_id, company),
        ).fetchall()
        return [self._activity_row_to_dict(r) for r in rows]

    def unique_activity_companies(self, account_id: UUID) -> set[str]:
        """Return deduplicated company names from one account's activity log."""
        rows = self.conn.execute(
            "SELECT DISTINCT company FROM activity_log WHERE account_id = %s AND company != ''",
            (account_id,),
        ).fetchall()
        return {r["company"] for r in rows}

    def application_counts_by_company(self, account_id: UUID) -> dict[str, int]:
        """How many `Application` rows this account has logged per company.

        Keys are lower-cased company display names so callers can join
        case-insensitively against `companies.name`. Other actions
        (Contact, Screen, Interview, Referral, Reach-out, Inquery) are
        excluded — only the act of applying counts here.
        """
        rows = self.conn.execute(
            """SELECT lower(company) AS company_lower, COUNT(*) AS n
               FROM activity_log
               WHERE account_id = %s AND action = 'Application' AND company != ''
               GROUP BY lower(company)""",
            (account_id,),
        ).fetchall()
        return {r["company_lower"]: r["n"] for r in rows}

    # -------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------

    def _hydrate_account(self, row: dict) -> Account:
        return Account(
            id=row["id"],
            provider=row["provider"],
            external_id=row["external_id"],
            email=row["email"],
            display_name=row["display_name"],
            handle=row["handle"],
        )

    def upsert_account_from_claims(
        self, provider: str, claims: dict[str, Any]
    ) -> Account:
        """Resolve or create the account row matching this OAuth identity.

        external_id picks the most stable claim per provider:
          - entra: `oid` (per-user-per-tenant, stable across rename) → fallback `sub`
          - github: `sub` (stringified numeric user id from GitHubProvider)

        email / display_name / handle are refreshed on every call from the
        current token — always reflects the most recent provider-side state.
        raw_claims captures the full last-seen claim set for forensics.
        """
        external_id = pick_external_id(provider, claims)
        if not external_id:
            raise ValueError(
                f"Cannot derive external_id from claims for provider={provider!r}: "
                f"missing oid/sub"
            )
        email = claims.get("email")
        display_name = claims.get("name")
        handle = claims.get("preferred_username") or claims.get("login") or claims.get("upn")
        # COALESCE on the snapshot fields so a token missing an optional
        # claim (Entra sometimes drops `email` for personal accounts; GitHub
        # `name` is null when the user hasn't set one) doesn't null out a
        # value the previous token gave us. raw_claims and last_seen_at
        # always overwrite — they're meant to reflect the latest token.
        row = self.conn.execute(
            """INSERT INTO accounts (provider, external_id, email, display_name, handle, raw_claims)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (provider, external_id) DO UPDATE SET
                   email = COALESCE(EXCLUDED.email, accounts.email),
                   display_name = COALESCE(EXCLUDED.display_name, accounts.display_name),
                   handle = COALESCE(EXCLUDED.handle, accounts.handle),
                   raw_claims = EXCLUDED.raw_claims,
                   last_seen_at = now()
               RETURNING *""",
            (provider, external_id, email, display_name, handle, Json(claims)),
        ).fetchone()
        assert row is not None
        return self._hydrate_account(row)

    def get_account(self, account_id: UUID) -> Account | None:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE id = %s", (account_id,),
        ).fetchone()
        return self._hydrate_account(row) if row else None

    # -------------------------------------------------------------------
    # CSV Migration
    # -------------------------------------------------------------------

    @staticmethod
    def _migrate_csv_activity_log(conn: psycopg.Connection[DictRow]) -> None:
        """One-time CSV activity log import. Called by `jsb migrate` only.

        Dead code path from normal JobStore usage — only invoked explicitly
        by the migrate CLI command.
        """
        row = conn.execute("SELECT COUNT(*) AS cnt FROM activity_log").fetchone()
        assert row is not None  # COUNT(*) always returns one row
        if row["cnt"] > 0:
            return

        try:
            from jobbuddy.settings import get_settings
            csv_path = get_settings().data_dir / "job-search-log.csv"
        except Exception:
            return

        if not csv_path.exists():
            return

        # CSV-imported rows have no authenticated owner, so attribute them to
        # the bootstrap account inserted by migration 015a. The operator can
        # reattribute them after their first authenticated MCP request using
        # the SQL documented in 015a's header comment.
        bootstrap_row = conn.execute(
            "SELECT id FROM accounts WHERE provider='local' AND external_id='bootstrap'"
        ).fetchone()
        if bootstrap_row is None:
            log.warning(
                "CSV activity log migration skipped: bootstrap account row missing — "
                "did migration 015a run?"
            )
            return
        bootstrap_id = bootstrap_row["id"]

        import csv
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                count = 0
                for r in reader:
                    conn.execute(
                        """INSERT INTO activity_log
                           (account_id, log_date, company, role, job_id, action, person, location, status, url, notes)
                           VALUES (%s, COALESCE(NULLIF(%s,'')::date, CURRENT_DATE), %s, %s,
                                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
                                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))""",
                        (
                            bootstrap_id,
                            r.get("date", ""),
                            r.get("company", ""),
                            r.get("role", ""),
                            r.get("job_id", ""),
                            r.get("action", ""),
                            r.get("person", ""),
                            r.get("location", ""),
                            r.get("status", ""),
                            r.get("url", ""),
                            r.get("notes", ""),
                        ),
                    )
                    count += 1
                if count:
                    log.info("Migrated %d rows from CSV activity log to PostgreSQL", count)
        except Exception as e:
            log.warning("CSV activity log migration failed: %s", e)


def pick_external_id(provider: str, claims: dict[str, Any]) -> str | None:
    """Pick the stable identifier from a token's claims for a given provider.

    For Entra, `oid` is required — it's stable per user across the tenant.
    `sub` is per-(user, app) and would change if the app registration is
    replaced, fragmenting one human across two account rows. We refuse
    that fallback rather than silently duplicating the row. FastMCP's
    AzureProvider also surfaces a filtered `upstream_claims` sub-dict;
    honor it as a fallback for callers handed only that subset.

    For GitHub, GitHubProvider sets `claims["sub"]` to the stringified
    numeric user id; that's what we key on.

    Empty strings are treated as missing — a malformed token with `oid=""`
    must not silently fall through. Returns None when no usable id is
    present, and the caller raises.
    """
    def nonempty(v: object) -> str | None:
        return v if isinstance(v, str) and v else None

    upstream = claims.get("upstream_claims") or {}
    if provider == "entra":
        oid = nonempty(claims.get("oid")) or nonempty(upstream.get("oid"))
        if oid is None:
            log.warning(
                "Entra token missing `oid` claim — refusing fallback to `sub` "
                "to avoid account-row duplication. Verify the token is an "
                "ID/access token from AzureProvider, not a stripped subset."
            )
        return oid
    if provider == "github":
        return nonempty(claims.get("sub")) or nonempty(upstream.get("sub"))
    return nonempty(claims.get("sub")) or nonempty(upstream.get("sub"))
