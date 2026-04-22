"""JobStore — PostgreSQL persistence layer for job listings and embeddings.

Schema uses a surrogate SERIAL PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id). Embeddings stored as pgvector vector(1536) column.
"""

import json
import logging
from datetime import date, datetime, timezone

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Json

from jobbuddy.models import Company, Job
from jobbuddy.types import EmbedWorkItem, StripWorkItem

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
    """PostgreSQL persistence for jobs and embeddings.

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

    def _connect(self, conninfo: str | None) -> psycopg.Connection:
        if conninfo is None:
            from jobbuddy.settings import pg_conninfo_with_token
            conninfo = pg_conninfo_with_token()

        conn = psycopg.connect(conninfo, autocommit=True)
        register_vector(conn)
        conn.row_factory = dict_row
        return conn

    # -------------------------------------------------------------------
    # Companies
    # -------------------------------------------------------------------

    def _hydrate_company(self, row: dict) -> Company:
        """Build a Company from a DB row."""
        config = row["config"] or {}
        return Company(
            slug=row["slug"], name=row["name"],
            ats=row["ats"], board=row["board"], **config,
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
        doesn't clobber existing ATS config."""
        extra = {
            k: v for k, v in company.model_dump().items()
            if k not in ("slug", "name", "ats", "board")
        }
        self.conn.execute(
            """INSERT INTO companies (slug, name, ats, board, config, content_hash)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (slug) DO UPDATE SET
                name = excluded.name,
                ats = COALESCE(excluded.ats, companies.ats),
                board = COALESCE(excluded.board, companies.board),
                config = CASE
                    WHEN excluded.ats IS NOT NULL THEN excluded.config
                    ELSE companies.config
                END,
                content_hash = excluded.content_hash""",
            (company.slug, company.name, company.ats, company.board, Json(extra),
             str(company.content_hash)),
        )

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
                    str(j.content_hash(None)),
                )
                for j in jobs
            ]
            with self.conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO jobs
                       (company_slug, job_id, title, location, url, published_at,
                        department, team, salary, description, ats_metadata, last_seen,
                        listing_status, content_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
                       ON CONFLICT(company_slug, job_id) DO UPDATE SET
                        title = excluded.title,
                        location = excluded.location,
                        url = excluded.url,
                        published_at = excluded.published_at,
                        department = excluded.department,
                        team = excluded.team,
                        salary = excluded.salary,
                        description = COALESCE(excluded.description, jobs.description),
                        ats_metadata = COALESCE(excluded.ats_metadata, jobs.ats_metadata),
                        last_seen = excluded.last_seen,
                        listing_status = 'active',
                        content_hash = md5(
                            coalesce(jobs.description_stripped, '')
                            || excluded.title
                            || coalesce(excluded.location, '')
                            || coalesce(excluded.department, '')
                        )::uuid
                       WHERE (jobs.title, jobs.location, jobs.url, jobs.published_at,
                              jobs.department, jobs.team, jobs.salary, jobs.description,
                              jobs.ats_metadata, jobs.listing_status)
                             IS DISTINCT FROM
                             (excluded.title, excluded.location, excluded.url, excluded.published_at,
                              excluded.department, excluded.team, excluded.salary,
                              COALESCE(excluded.description, jobs.description),
                              COALESCE(excluded.ats_metadata, jobs.ats_metadata),
                              'active')""",
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
        company: str | None = None,
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
            company=company, exclude_companies=exclude_companies,
            title=title, location=location, posted_after=posted_after,
        )

        if include_removed:
            conditions = [c for c in conditions if c != "j.listing_status = 'active'"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if company:
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
        return [dict(row) for row in rows]

    def get_jobs_needing_descriptions(self, slug: str) -> list[dict]:
        """Return active jobs with NULL description for a company."""
        rows = self.conn.execute(
            """SELECT job_id, title, ats_metadata FROM jobs
               WHERE company_slug = %s AND description IS NULL AND listing_status = 'active'""",
            (slug,),
        ).fetchall()
        return [dict(row) for row in rows]

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

    def _stripping_conditions(self, slugs: list[str] | None = None) -> tuple[str, list]:
        conditions = [
            "description IS NOT NULL",
            "description_stripped IS NULL",
            "listing_status = 'active'",
        ]
        params: list = []
        if slugs:
            conditions.append("company_slug = ANY(%s)")
            params.append(slugs)
        return " AND ".join(conditions), params

    def count_jobs_needing_stripping(self, *, slugs: list[str] | None = None) -> int:
        """Count active jobs with descriptions but no stripped version."""
        where, params = self._stripping_conditions(slugs)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM jobs WHERE {where}", params
        ).fetchone()
        return row["cnt"]

    def get_jobs_needing_stripping(self, limit: int = 50, *, slugs: list[str] | None = None) -> list[StripWorkItem]:
        """Return active jobs with descriptions but no stripped version."""
        where, params = self._stripping_conditions(slugs)
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT id, company_slug, job_id, title, description FROM jobs
               WHERE {where}
               LIMIT %s""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]  # type: ignore[return-value]

    def update_stripped_description(self, job_pk: int, stripped: str) -> None:
        """Set the stripped description for a job.

        Empty/whitespace-only strings are stored as NULL so the job gets
        re-stripped on the next pass. Recomputes content_hash so the embed
        phase detects the change via hash mismatch.
        """
        value = stripped.strip() if stripped else None
        if not value:
            value = None
        self.conn.execute(
            """UPDATE jobs SET
                description_stripped = %s,
                content_hash = md5(
                    coalesce(%s, '') || title || coalesce(location, '') || coalesce(department, '')
                )::uuid
               WHERE id = %s""",
            (value, value, job_pk),
        )

    def clear_stripped_descriptions(self) -> int:
        """Set description_stripped to NULL for all jobs. Returns count affected.

        Deletes embeddings for affected jobs and recomputes content_hash.
        """
        with self.conn.transaction():
            self.conn.execute("""
                DELETE FROM job_embeddings
                WHERE job_id IN (SELECT id FROM jobs WHERE description_stripped IS NOT NULL)
            """)
            cur = self.conn.execute("""
                UPDATE jobs SET
                    description_stripped = NULL,
                    content_hash = md5(
                        '' || title || coalesce(location, '') || coalesce(department, '')
                    )::uuid
                WHERE description_stripped IS NOT NULL
            """)
            return cur.rowcount

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

    # -------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------

    def store_embedding(self, job_id: int, job_hash: str, company_hash: str,
                        embedding: list[float]) -> None:
        """Store a single embedding in job_embeddings (chunk_index=0)."""
        self.store_embeddings([(job_id, job_hash, company_hash, embedding)])

    def store_embeddings(self, items: list[tuple[int, str, str, list[float]]]) -> None:
        """Batch store embeddings in job_embeddings.

        Each item is (job_id, job_hash, company_hash, vector).
        Deletes old chunks per job_id first, then inserts new ones.
        """
        if not items:
            return
        job_ids = [i[0] for i in items]
        with self.conn.transaction():
            self.conn.execute(
                "DELETE FROM job_embeddings WHERE job_id = ANY(%s)", (job_ids,)
            )
            with self.conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO job_embeddings (job_id, chunk_index, job_hash, company_hash, embedding)
                       VALUES (%s, 0, %s, %s, %s)""",
                    [(jid, jh, ch, vec) for jid, jh, ch, vec in items],
                    returning=False,
                )

    def delete_embedding(self, job_id: int) -> None:
        """Delete all embeddings for a job."""
        self.conn.execute(
            "DELETE FROM job_embeddings WHERE job_id = %s", (job_id,)
        )

    def search_similar(self, query_embedding: list[float], k: int = 25) -> list[dict]:
        """KNN search via pgvector, returns job dicts with distance score."""
        rows = self.conn.execute("""
            SELECT e.embedding <=> %s::vector AS distance,
                   j.*, s.last_sync, c.name AS company_name
            FROM job_embeddings e
            JOIN jobs j ON e.job_id = j.id
            LEFT JOIN sync_status s ON j.company_slug = s.company_slug
            LEFT JOIN companies c ON j.company_slug = c.slug
            WHERE j.listing_status = 'active'
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
        """, (query_embedding, query_embedding, k)).fetchall()
        return [dict(r) for r in rows]

    def _build_filter_conditions(
        self,
        *,
        company: str | None = None,
        exclude_companies: list[str] | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
    ) -> tuple[list[str], list]:
        """Build WHERE conditions and params for job search filters."""
        conditions = ["j.listing_status = 'active'"]
        params: list = []

        if company:
            conditions.append("j.company_slug = %s")
            params.append(company)

        if exclude_companies:
            conditions.append("j.company_slug != ALL(%s)")
            params.append(exclude_companies)

        if posted_after:
            conditions.append("j.published_at >= %s")
            params.append(posted_after)

        if title:
            terms = [t.strip() for t in title.split(",") if t.strip()]
            if terms:
                or_clauses = ["j.title ILIKE %s"] * len(terms)
                conditions.append(f"({' OR '.join(or_clauses)})")
                params.extend(f"%{t}%" for t in terms)

        if location:
            terms = [t.strip() for t in location.split(",") if t.strip()]
            if terms:
                or_clauses = ["j.location ILIKE %s"] * len(terms)
                conditions.append(f"({' OR '.join(or_clauses)})")
                params.extend(f"%{t}%" for t in terms)

        return conditions, params

    DIVERSITY_FLOOR_MULTIPLIER = 1.20
    DIVERSITY_POOL_SIZE = 500

    def search_similar_filtered(
        self,
        query_embedding: list[float],
        *,
        company: str | None = None,
        exclude_companies: list[str] | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        k: int = 25,
    ) -> list[dict]:
        """KNN search with filters, relevance floor, and round-robin diversity.

        Fetches a pool of candidates, applies a dynamic relevance floor
        (best_distance * 1.20), then round-robins across companies so no
        single employer dominates results.
        """
        conditions, params = self._build_filter_conditions(
            company=company, exclude_companies=exclude_companies,
            title=title, location=location, posted_after=posted_after,
        )

        pool_size = max(self.DIVERSITY_POOL_SIZE, k * 5)
        vec_params = [query_embedding] + params + [query_embedding, pool_size]
        where = " AND ".join(conditions)

        sql = f"""
            WITH base AS (
                SELECT e.embedding <=> %s::vector AS distance,
                       j.*, s.last_sync, c.name AS company_name
                FROM job_embeddings e
                JOIN jobs j ON e.job_id = j.id
                LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                LEFT JOIN companies c ON j.company_slug = c.slug
                WHERE {where}
                ORDER BY e.embedding <=> %s::vector
                LIMIT %s
            ),
            floored AS (
                SELECT * FROM base
                WHERE distance <= (SELECT min(distance) * %s FROM base)
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY company_slug ORDER BY distance
                       ) AS rn
                FROM floored
            )
            SELECT * FROM ranked
            ORDER BY rn, distance
            LIMIT %s
        """
        vec_params.extend([self.DIVERSITY_FLOOR_MULTIPLIER, k])

        rows = self.conn.execute(sql, vec_params).fetchall()
        return [dict(r) for r in rows]

    def _embedding_base_query(self, slugs: list[str] | None = None) -> tuple[str, str, list]:
        """Base FROM/WHERE for jobs needing embeddings (hash mismatch or missing)."""
        extra_conditions = []
        params: list = []
        if slugs:
            extra_conditions.append("j.company_slug = ANY(%s)")
            params.append(slugs)
        extra = (" AND " + " AND ".join(extra_conditions)) if extra_conditions else ""

        from_clause = """
            jobs j
            JOIN companies c ON j.company_slug = c.slug
            LEFT JOIN job_embeddings e ON e.job_id = j.id AND e.chunk_index = 0
        """
        where_clause = f"""
            j.listing_status = 'active'
            AND j.description_stripped IS NOT NULL
            AND (e.id IS NULL OR e.job_hash IS DISTINCT FROM j.content_hash
                 OR e.company_hash IS DISTINCT FROM c.content_hash)
            {extra}
        """
        return from_clause, where_clause, params

    def count_jobs_needing_embeddings(self, slugs: list[str] | None = None) -> int:
        """Count jobs needing embeddings (missing or hash mismatch)."""
        from_clause, where_clause, params = self._embedding_base_query(slugs)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM {from_clause} WHERE {where_clause}", params
        ).fetchone()
        return row["cnt"]

    def list_jobs_needing_embeddings(self, slugs: list[str] | None = None, limit: int = 0) -> list[EmbedWorkItem]:
        """Jobs needing embeddings (missing or hash mismatch)."""
        from_clause, where_clause, params = self._embedding_base_query(slugs)

        sql = f"""
            SELECT j.id, j.content_hash AS job_hash, c.content_hash AS company_hash,
                   j.company_slug, j.job_id, j.title, j.department, j.location,
                   j.description_stripped
            FROM {from_clause}
            WHERE {where_clause}
        """
        if limit > 0:
            sql += " LIMIT %s"
            params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]  # type: ignore[return-value]

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
        """Total number of cached jobs."""
        sql = "SELECT COUNT(*) as cnt FROM jobs"
        if not include_removed:
            sql += " WHERE listing_status = 'active'"
        row = self.conn.execute(sql).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------

    def cache_exists(self) -> bool:
        """Check if there are any jobs in the cache."""
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
               (log_date, company, role, job_id, action, person, location, status, url, notes)
               VALUES (COALESCE(%s::date, CURRENT_DATE), %s, %s, NULLIF(%s,''), NULLIF(%s,''),
                       NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))
               RETURNING *""",
            (row_date, company, role, job_id, action, person, location, status, url, notes),
        ).fetchone()
        return self._activity_row_to_dict(row)

    def read_activity_log(self) -> list[dict]:
        """Read all activity log rows, most recent first."""
        rows = self.conn.execute(
            "SELECT * FROM activity_log ORDER BY log_date DESC, id DESC"
        ).fetchall()
        return [self._activity_row_to_dict(r) for r in rows]

    def find_activity_duplicates(
        self, url: str = "", company: str = "", role: str = ""
    ) -> list[dict]:
        """Find rows matching a URL, or company+role combo."""
        if url:
            rows = self.conn.execute(
                "SELECT * FROM activity_log WHERE url = %s ORDER BY log_date DESC, id DESC",
                (url.strip(),),
            ).fetchall()
        elif company and role:
            rows = self.conn.execute(
                """SELECT * FROM activity_log
                   WHERE lower(company) = lower(%s) AND lower(role) = lower(%s)
                   ORDER BY log_date DESC, id DESC""",
                (company, role),
            ).fetchall()
        else:
            return []
        return [self._activity_row_to_dict(r) for r in rows]

    def find_activity_by_company(self, company: str) -> list[dict]:
        """Find all activity rows for a company (case-insensitive)."""
        rows = self.conn.execute(
            """SELECT * FROM activity_log
               WHERE lower(company) = lower(%s)
               ORDER BY log_date DESC, id DESC""",
            (company,),
        ).fetchall()
        return [self._activity_row_to_dict(r) for r in rows]

    def unique_activity_companies(self) -> set[str]:
        """Return deduplicated company names from the activity log."""
        rows = self.conn.execute(
            "SELECT DISTINCT company FROM activity_log WHERE company != ''"
        ).fetchall()
        return {r["company"] for r in rows}

    # -------------------------------------------------------------------
    # CSV Migration
    # -------------------------------------------------------------------

    def _migrate_csv_activity_log(self, conn: psycopg.Connection) -> None:
        """One-time CSV activity log import. Called by `jsb migrate` only.

        Dead code path from normal JobStore usage — only invoked explicitly
        by the migrate CLI command.
        """
        row = conn.execute("SELECT COUNT(*) AS cnt FROM activity_log").fetchone()
        if row["cnt"] > 0:
            return

        try:
            from jobbuddy.settings import get_settings
            csv_path = get_settings().data_dir / "job-search-log.csv"
        except Exception:
            return

        if not csv_path.exists():
            return

        import csv
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                count = 0
                for r in reader:
                    conn.execute(
                        """INSERT INTO activity_log
                           (log_date, company, role, job_id, action, person, location, status, url, notes)
                           VALUES (COALESCE(NULLIF(%s,'')::date, CURRENT_DATE), %s, %s,
                                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
                                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))""",
                        (
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
