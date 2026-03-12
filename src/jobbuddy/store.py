"""JobStore — PostgreSQL persistence layer for job listings and embeddings.

Schema uses a surrogate SERIAL PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id). Embeddings stored as pgvector vector(1536) column.
"""

import json
import logging
from datetime import datetime, timezone
from typing import ClassVar

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from jobbuddy.models import Job
from jobbuddy.types import EmbedWorkItem, StripWorkItem

log = logging.getLogger(__name__)


def _utcnow() -> str:
    """ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _validate_date(value: str | None) -> str | None:
    """Validate YYYY-MM-DD format. Returns None for malformed values."""
    if not value:
        return None
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
        return value[:10]
    except (ValueError, TypeError):
        return None


class JobStore:
    """PostgreSQL persistence for jobs and embeddings.

    Args:
        conninfo: psycopg connection string, or None for default from settings.
    """

    _schema_initialized: ClassVar[set[str]] = set()

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
            from jobbuddy.settings import get_settings
            conninfo = get_settings().pg_conninfo

        conn = psycopg.connect(conninfo, autocommit=True)
        register_vector(conn)
        conn.row_factory = dict_row

        if conninfo not in JobStore._schema_initialized:
            self._ensure_schema(conn)
            JobStore._schema_initialized.add(conninfo)

        return conn

    def _ensure_schema(self, conn: psycopg.Connection) -> None:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              SERIAL PRIMARY KEY,
                company_slug    TEXT NOT NULL,
                job_id          TEXT NOT NULL,
                title           TEXT NOT NULL,
                location        TEXT,
                url             TEXT,
                published_at    DATE,
                department      TEXT,
                team            TEXT,
                salary          TEXT,
                description     TEXT,
                description_stripped TEXT CHECK(description_stripped IS NULL OR LENGTH(description_stripped) > 0),
                ats_metadata    JSONB,
                embedding       vector(1536),
                last_seen       TIMESTAMPTZ NOT NULL,
                disappeared_at  TIMESTAMPTZ,
                UNIQUE (company_slug, job_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_status (
                company_slug    TEXT PRIMARY KEY,
                last_sync       TIMESTAMPTZ NOT NULL,
                job_count       INTEGER NOT NULL DEFAULT 0,
                error           TEXT
            )
        """)

        # Create indexes (IF NOT EXISTS)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs (company_slug)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_published ON jobs (published_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_disappeared ON jobs (disappeared_at) WHERE disappeared_at IS NULL
        """)

        # HNSW index for vector similarity search
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_embedding ON jobs
                USING hnsw (embedding vector_cosine_ops)
        """)

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

            gone = current_ids - new_ids
            if gone:
                for jid in gone:
                    self.conn.execute(
                        """UPDATE jobs SET disappeared_at = %s
                           WHERE company_slug = %s AND job_id = %s AND disappeared_at IS NULL""",
                        (now, slug, jid),
                    )

            for j in jobs:
                self.conn.execute(
                    """INSERT INTO jobs
                       (company_slug, job_id, title, location, url, published_at,
                        department, team, salary, description, ats_metadata, last_seen, disappeared_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
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
                        disappeared_at = NULL""",
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
                    ),
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
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        include_disappeared: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        params: list = []

        if not include_disappeared:
            conditions.append("j.disappeared_at IS NULL")

        if company:
            conditions.append("j.company_slug = %s")
            params.append(company)

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

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql = f"""
            SELECT j.*, s.last_sync
            FROM jobs j
            LEFT JOIN sync_status s ON j.company_slug = s.company_slug
            {where}
            ORDER BY j.published_at DESC NULLS LAST
            LIMIT %s
        """
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_jobs_needing_descriptions(self, slug: str) -> list[dict]:
        """Return active jobs with NULL description for a company."""
        rows = self.conn.execute(
            """SELECT job_id, title, ats_metadata FROM jobs
               WHERE company_slug = %s AND description IS NULL AND disappeared_at IS NULL""",
            (slug,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_descriptions(self, slug: str, descs: dict[str, str]) -> None:
        """Batch update description column for jobs in a company."""
        if not descs:
            return

        with self.conn.transaction():
            for job_id, desc in descs.items():
                self.conn.execute(
                    "UPDATE jobs SET description = %s WHERE company_slug = %s AND job_id = %s",
                    (desc, slug, job_id),
                )

    def _stripping_conditions(self, slugs: list[str] | None = None) -> tuple[str, list]:
        conditions = [
            "description IS NOT NULL",
            "description_stripped IS NULL",
            "disappeared_at IS NULL",
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
        re-stripped on the next pass. Also nullifies any existing embedding
        so the job gets re-embedded with the new stripped text.
        """
        value = stripped.strip() if stripped else None
        if not value:
            value = None
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE jobs SET description_stripped = %s WHERE id = %s",
                (value, job_pk),
            )
            self.conn.execute(
                "UPDATE jobs SET embedding = NULL WHERE id = %s", (job_pk,)
            )

    def clear_stripped_descriptions(self) -> int:
        """Set description_stripped to NULL for all jobs. Returns count affected.

        Embeddings for affected jobs are also cleared.
        """
        with self.conn.transaction():
            self.conn.execute("""
                UPDATE jobs SET embedding = NULL
                WHERE description_stripped IS NOT NULL
            """)
            cur = self.conn.execute(
                "UPDATE jobs SET description_stripped = NULL WHERE description_stripped IS NOT NULL"
            )
            return cur.rowcount

    def get_job_by_ids(self, job_ids: list[int]) -> list[dict]:
        """Fetch jobs by surrogate key IDs."""
        if not job_ids:
            return []
        rows = self.conn.execute(
            """SELECT j.*, s.last_sync
                FROM jobs j
                LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                WHERE j.id = ANY(%s)""",
            (job_ids,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------

    def store_embedding(self, job_id: int, embedding: list[float]) -> None:
        """Store embedding vector on the jobs row."""
        self.conn.execute(
            "UPDATE jobs SET embedding = %s WHERE id = %s",
            (embedding, job_id),
        )

    def delete_embedding(self, job_id: int) -> None:
        """Clear embedding for a job."""
        self.conn.execute(
            "UPDATE jobs SET embedding = NULL WHERE id = %s", (job_id,)
        )

    def search_similar(self, query_embedding: list[float], k: int = 25) -> list[dict]:
        """KNN search via pgvector, returns job dicts with distance score."""
        rows = self.conn.execute("""
            SELECT embedding <=> %s::vector AS distance,
                   j.*, s.last_sync
            FROM jobs j
            LEFT JOIN sync_status s ON j.company_slug = s.company_slug
            WHERE j.embedding IS NOT NULL
              AND j.disappeared_at IS NULL
            ORDER BY j.embedding <=> %s::vector
            LIMIT %s
        """, (query_embedding, query_embedding, k)).fetchall()
        return [dict(r) for r in rows]

    def search_similar_filtered(
        self,
        query_embedding: list[float],
        *,
        company: str | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        k: int = 25,
    ) -> list[dict]:
        """KNN search with filters on company/title/location."""
        conditions = [
            "j.disappeared_at IS NULL",
            "j.embedding IS NOT NULL",
        ]
        params: list = [query_embedding]

        if company:
            conditions.append("j.company_slug = %s")
            params.append(company)

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

        params.extend([query_embedding, k])
        where = " AND ".join(conditions)

        sql = f"""
            SELECT j.embedding <=> %s::vector AS distance,
                   j.*, s.last_sync
            FROM jobs j
            LEFT JOIN sync_status s ON j.company_slug = s.company_slug
            WHERE {where}
            ORDER BY j.embedding <=> %s::vector
            LIMIT %s
        """

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _embedding_conditions(self, slugs: list[str] | None = None) -> tuple[str, list]:
        """WHERE clause for jobs needing embeddings."""
        conditions = [
            "description_stripped IS NOT NULL",
            "disappeared_at IS NULL",
            "embedding IS NULL",
        ]
        params: list = []
        if slugs:
            conditions.append("company_slug = ANY(%s)")
            params.append(slugs)
        return " AND ".join(conditions), params

    def count_jobs_needing_embeddings(self, slugs: list[str] | None = None) -> int:
        """Count jobs with stripped descriptions but no embedding."""
        where, params = self._embedding_conditions(slugs)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM jobs WHERE {where}", params
        ).fetchone()
        return row["cnt"]

    def list_jobs_needing_embeddings(self, slugs: list[str] | None = None, limit: int = 0) -> list[EmbedWorkItem]:
        """Jobs with stripped descriptions but no embedding."""
        where, params = self._embedding_conditions(slugs)

        sql = f"""
            SELECT id, company_slug, job_id, title,
                   department, location, description_stripped
            FROM jobs
            WHERE {where}
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

    def job_count(self, include_disappeared: bool = False) -> int:
        """Total number of cached jobs."""
        sql = "SELECT COUNT(*) as cnt FROM jobs"
        if not include_disappeared:
            sql += " WHERE disappeared_at IS NULL"
        row = self.conn.execute(sql).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------

    def cache_exists(self) -> bool:
        """Check if there are any jobs in the cache."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
        return row["cnt"] > 0 if row else False
