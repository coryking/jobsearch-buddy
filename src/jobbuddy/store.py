"""JobStore — SQLite persistence layer for job listings and embeddings.

Schema uses a surrogate INTEGER PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id). Embeddings stored as BLOBs in job_embeddings,
with a vec0 virtual table (sqlite-vec) for cosine similarity search.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import sqlite_vec

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
    """SQLite persistence for jobs and embeddings.

    Args:
        db_path: Path to SQLite DB, ":memory:" for in-memory, or None for default.
    """

    _schema_initialized: ClassVar[set[str]] = set()  # tracks db_path strings

    def __init__(self, db_path: Path | str | None = None):
        self.conn = self._connect(db_path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -------------------------------------------------------------------
    # Connection + Schema
    # -------------------------------------------------------------------

    def _connect(self, db_path: Path | str | None) -> sqlite3.Connection:
        if db_path is None:
            from jobbuddy.settings import get_settings
            db_path = get_settings().db_path

        resolved = Path(db_path) if str(db_path) != ":memory:" else db_path
        if isinstance(resolved, Path):
            resolved.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        db_key = str(db_path)
        if db_key == ":memory:" or db_key not in JobStore._schema_initialized:
            self._ensure_schema(conn)
            if db_key != ":memory:":
                JobStore._schema_initialized.add(db_key)

        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables or migrate from old schema."""
        # Check if jobs table exists
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()

        if not table_exists:
            self._create_tables(conn)
        else:
            # Check if it needs migration (old schema has composite PK, no 'id' column)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "id" not in cols:
                self._migrate_jobs_table(conn)
            else:
                # Run column migrations for any missing columns
                self._run_column_migrations(conn)

        self._init_embedding_tables(conn)
        conn.commit()

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                company_slug   TEXT NOT NULL,
                job_id         TEXT NOT NULL,
                title          TEXT NOT NULL,
                location       TEXT,
                url            TEXT,
                published_at   DATE,
                department     TEXT,
                team           TEXT,
                salary         TEXT,
                description    TEXT,
                description_stripped TEXT,
                ats_metadata   TEXT,
                last_seen      TIMESTAMP NOT NULL,
                disappeared_at TIMESTAMP,
                UNIQUE (company_slug, job_id)
            );

            CREATE TABLE IF NOT EXISTS sync_status (
                company_slug TEXT PRIMARY KEY,
                last_sync    TIMESTAMP NOT NULL,
                job_count    INTEGER NOT NULL DEFAULT 0,
                error        TEXT
            );
        """)

    def _migrate_jobs_table(self, conn: sqlite3.Connection) -> None:
        """Migrate old composite-PK jobs table to surrogate key schema."""
        log.info("Migrating jobs table to surrogate key schema")

        # First ensure all columns exist on the old table
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        migrations = [
            ("disappeared_at", "TIMESTAMP"),
            ("description", "TEXT"),
            ("ats_metadata", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name not in cols:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")

        conn.execute("""
            CREATE TABLE jobs_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                company_slug   TEXT NOT NULL,
                job_id         TEXT NOT NULL,
                title          TEXT NOT NULL,
                location       TEXT,
                url            TEXT,
                published_at   DATE,
                department     TEXT,
                team           TEXT,
                salary         TEXT,
                description    TEXT,
                ats_metadata   TEXT,
                last_seen      TIMESTAMP NOT NULL,
                disappeared_at TIMESTAMP,
                UNIQUE (company_slug, job_id)
            )
        """)

        conn.execute("""
            INSERT INTO jobs_new (company_slug, job_id, title, location, url,
                published_at, department, team, salary, description, ats_metadata,
                last_seen, disappeared_at)
            SELECT company_slug, job_id, title, location, url,
                published_at, department, team, salary, description, ats_metadata,
                last_seen, disappeared_at
            FROM jobs
        """)

        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_new RENAME TO jobs")

        # Drop old job_embeddings table (will be recreated with new schema)
        conn.execute("DROP TABLE IF EXISTS job_embeddings")

        conn.commit()
        log.info("Migration complete")

    def _run_column_migrations(self, conn: sqlite3.Connection) -> None:
        """Add any missing columns to the jobs table."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        migrations = [
            ("disappeared_at", "TIMESTAMP"),
            ("description", "TEXT"),
            ("ats_metadata", "TEXT"),
            ("description_stripped", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name not in cols:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
        conn.commit()

    def _init_embedding_tables(self, conn: sqlite3.Connection) -> None:
        # Migration: drop old multi-model schema (all old embeddings are discarded)
        old_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='embedding_models'"
        ).fetchone()
        if old_table:
            log.info("Dropping old embedding_models/job_embeddings tables (single-model migration)")
            conn.execute("DROP TABLE IF EXISTS job_embeddings")
            conn.execute("DROP TABLE IF EXISTS embedding_models")

        # Migration: drop text_hash column if present (old schema)
        existing_emb = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_embeddings'"
        ).fetchone()
        if existing_emb:
            emb_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_embeddings)").fetchall()}
            if "text_hash" in emb_cols:
                log.info("Migrating job_embeddings: dropping text_hash column")
                conn.execute("""
                    CREATE TABLE job_embeddings_new (
                        job_id    INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                        embedding BLOB NOT NULL
                    )
                """)
                conn.execute("INSERT INTO job_embeddings_new SELECT job_id, embedding FROM job_embeddings")
                conn.execute("DROP TABLE job_embeddings")
                conn.execute("ALTER TABLE job_embeddings_new RENAME TO job_embeddings")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS job_embeddings (
                job_id    INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                embedding BLOB NOT NULL
            );
        """)

        # vec0 virtual table for cosine similarity search.
        # Migration: old schema used 'rowid' instead of 'job_id'. Detect and recreate.
        vec_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_jobs'"
        ).fetchone()
        if vec_exists:
            cols = {row[1] for row in conn.execute("PRAGMA table_xinfo(vec_jobs)").fetchall()}
            if "job_id" not in cols:
                log.info("Recreating vec_jobs (schema migration: rowid → job_id)")
                conn.execute("DROP TABLE vec_jobs")
                vec_exists = None
        if not vec_exists:
            conn.execute("""
                CREATE VIRTUAL TABLE vec_jobs USING vec0(
                    job_id INTEGER PRIMARY KEY,
                    embedding float[1536] distance_metric=cosine
                )
            """)
            # Backfill vec_jobs from job_embeddings (migration or fresh table)
            count = conn.execute("SELECT COUNT(*) FROM job_embeddings").fetchone()[0]
            if count:
                log.info("Backfilling vec_jobs from job_embeddings (%d rows)", count)
                conn.execute("""
                    INSERT INTO vec_jobs(job_id, embedding)
                    SELECT job_id, embedding FROM job_embeddings
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

        with self.conn:
            current_ids = {
                row["job_id"]
                for row in self.conn.execute(
                    "SELECT job_id FROM jobs WHERE company_slug = ?", (slug,)
                ).fetchall()
            }
            new_ids = {j.id for j in jobs}

            gone = current_ids - new_ids
            if gone:
                self.conn.executemany(
                    """UPDATE jobs SET disappeared_at = ?
                       WHERE company_slug = ? AND job_id = ? AND disappeared_at IS NULL""",
                    [(now, slug, jid) for jid in gone],
                )

            self.conn.executemany(
                """INSERT INTO jobs
                   (company_slug, job_id, title, location, url, published_at,
                    department, team, salary, description, ats_metadata, last_seen, disappeared_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                [
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
                ],
            )

            self.conn.execute(
                """INSERT OR REPLACE INTO sync_status
                   (company_slug, last_sync, job_count, error)
                   VALUES (?, ?, ?, NULL)""",
                (slug, now, len(jobs)),
            )

    def query_jobs(
        self,
        *,
        company: str | None = None,
        title: str | None = None,
        location: str | None = None,
        include_disappeared: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Query cached jobs with optional LIKE filters."""
        conditions = []
        params: list[str] = []

        if not include_disappeared:
            conditions.append("j.disappeared_at IS NULL")

        if company:
            conditions.append("j.company_slug = ?")
            params.append(company)

        if title:
            terms = [t.strip() for t in title.split(",") if t.strip()]
            if terms:
                or_clauses = ["j.title LIKE ? COLLATE NOCASE"] * len(terms)
                conditions.append(f"({' OR '.join(or_clauses)})")
                params.extend(f"%{t}%" for t in terms)

        if location:
            terms = [t.strip() for t in location.split(",") if t.strip()]
            if terms:
                or_clauses = ["j.location LIKE ? COLLATE NOCASE"] * len(terms)
                conditions.append(f"({' OR '.join(or_clauses)})")
                params.extend(f"%{t}%" for t in terms)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql = f"""
            SELECT j.*, s.last_sync
            FROM jobs j
            LEFT JOIN sync_status s ON j.company_slug = s.company_slug
            {where}
            ORDER BY j.published_at DESC NULLS LAST
            LIMIT ?
        """
        params.append(str(limit))

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_jobs_needing_descriptions(self, slug: str) -> list[dict]:
        """Return active jobs with NULL description for a company."""
        rows = self.conn.execute(
            """SELECT job_id, title, ats_metadata FROM jobs
               WHERE company_slug = ? AND description IS NULL AND disappeared_at IS NULL""",
            (slug,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_descriptions(self, slug: str, descs: dict[str, str]) -> None:
        """Batch update description column for jobs in a company."""
        if not descs:
            return

        with self.conn:
            self.conn.executemany(
                "UPDATE jobs SET description = ? WHERE company_slug = ? AND job_id = ?",
                [(desc, slug, job_id) for job_id, desc in descs.items()],
            )

    def _stripping_conditions(self, slug: str | None = None) -> tuple[str, list[str]]:
        conditions = [
            "description IS NOT NULL",
            "description_stripped IS NULL",
            "disappeared_at IS NULL",
        ]
        params: list[str] = []
        if slug:
            conditions.append("company_slug = ?")
            params.append(slug)
        return " AND ".join(conditions), params

    def count_jobs_needing_stripping(self, *, slug: str | None = None) -> int:
        """Count active jobs with descriptions but no stripped version."""
        where, params = self._stripping_conditions(slug)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM jobs WHERE {where}", params
        ).fetchone()
        return row[0]

    def get_jobs_needing_stripping(self, limit: int = 50, *, slug: str | None = None) -> list[StripWorkItem]:
        """Return active jobs with descriptions but no stripped version."""
        where, params = self._stripping_conditions(slug)
        all_params: list[str | int] = list(params)
        all_params.append(limit)
        rows = self.conn.execute(
            f"""SELECT id, company_slug, job_id, title, description FROM jobs
               WHERE {where}
               LIMIT ?""",
            all_params,
        ).fetchall()
        return [dict(row) for row in rows]  # type: ignore[return-value]

    def update_stripped_description(self, job_pk: int, stripped: str) -> None:
        """Set the stripped description for a job.

        Deletes any existing embedding so the job gets re-embedded with the
        new stripped text on the next embed pass.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET description_stripped = ? WHERE id = ?",
                (stripped, job_pk),
            )
            # Cascade: invalidate stale embedding.  vec_jobs is cleaned up
            # by ON DELETE CASCADE on job_embeddings.
            self.conn.execute(
                "DELETE FROM job_embeddings WHERE job_id = ?", (job_pk,)
            )
            self.conn.execute(
                "DELETE FROM vec_jobs WHERE job_id = ?", (job_pk,)
            )

    def clear_stripped_descriptions(self) -> int:
        """Set description_stripped to NULL for all jobs. Returns count affected.

        Embeddings for affected jobs are deleted (no stripped text = nothing to embed).
        """
        with self.conn:
            # Delete embeddings for jobs that have stripped descriptions
            # (those are the ones about to be cleared)
            self.conn.execute("""
                DELETE FROM job_embeddings WHERE job_id IN (
                    SELECT id FROM jobs WHERE description_stripped IS NOT NULL
                )
            """)
            self.conn.execute("""
                DELETE FROM vec_jobs WHERE job_id IN (
                    SELECT id FROM jobs WHERE description_stripped IS NOT NULL
                )
            """)
            cur = self.conn.execute(
                "UPDATE jobs SET description_stripped = NULL WHERE description_stripped IS NOT NULL"
            )
            return cur.rowcount

    def get_job_by_ids(self, job_ids: list[int]) -> list[dict]:
        """Fetch jobs by surrogate key IDs."""
        if not job_ids:
            return []
        placeholders = ",".join("?" * len(job_ids))
        rows = self.conn.execute(
            f"""SELECT j.*, s.last_sync
                FROM jobs j
                LEFT JOIN sync_status s ON j.company_slug = s.company_slug
                WHERE j.id IN ({placeholders})""",
            job_ids,
        ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------

    def store_embedding(self, job_id: int, embedding: bytes) -> None:
        """Dual-write: job_embeddings + vec_jobs (search index)."""
        self.conn.execute("""
            INSERT INTO job_embeddings (job_id, embedding)
            VALUES (?, ?)
            ON CONFLICT(job_id) DO UPDATE SET embedding = excluded.embedding
        """, (job_id, embedding))
        self.conn.execute("""
            INSERT OR REPLACE INTO vec_jobs(job_id, embedding) VALUES (?, ?)
        """, (job_id, embedding))
        self.conn.commit()

    def delete_embedding(self, job_id: int) -> None:
        """Delete an embedding for a job."""
        self.conn.execute("DELETE FROM job_embeddings WHERE job_id = ?", (job_id,))
        self.conn.execute("DELETE FROM vec_jobs WHERE job_id = ?", (job_id,))
        self.conn.commit()

    def search_similar(self, query_embedding: bytes, k: int = 25) -> list[dict]:
        """KNN search via sqlite-vec, returns job dicts with distance score."""
        rows = self.conn.execute("""
            SELECT v.job_id, v.distance,
                   j.company_slug, j.job_id as ats_job_id, j.title,
                   j.location, j.url, j.description, j.department,
                   j.team, j.salary, j.published_at, j.ats_metadata,
                   j.disappeared_at
            FROM vec_jobs v
            JOIN jobs j ON j.id = v.job_id
            WHERE v.embedding MATCH ?
              AND v.k = ?
            ORDER BY v.distance
        """, (query_embedding, k)).fetchall()
        return [dict(r) for r in rows]

    def _embedding_conditions(self, slug: str | None = None) -> tuple[str, list[str]]:
        """WHERE clause for jobs needing embeddings: has stripped text, no embedding row."""
        conditions = [
            "j.description_stripped IS NOT NULL",
            "j.disappeared_at IS NULL",
            "e.job_id IS NULL",
        ]
        params: list[str] = []
        if slug:
            conditions.append("j.company_slug = ?")
            params.append(slug)
        return " AND ".join(conditions), params

    def count_jobs_needing_embeddings(self, slug: str | None = None) -> int:
        """Count jobs with stripped descriptions but no embedding."""
        where, params = self._embedding_conditions(slug)
        row = self.conn.execute(f"""
            SELECT COUNT(*) FROM jobs j
            LEFT JOIN job_embeddings e ON j.id = e.job_id
            WHERE {where}
        """, params).fetchone()
        return row[0]

    def list_jobs_needing_embeddings(self, slug: str | None = None, limit: int = 0) -> list[EmbedWorkItem]:
        """Jobs with stripped descriptions but no embedding."""
        where, params = self._embedding_conditions(slug)

        sql = f"""
            SELECT j.id, j.company_slug, j.job_id, j.title,
                   j.department, j.location, j.description_stripped
            FROM jobs j
            LEFT JOIN job_embeddings e ON j.id = e.job_id
            WHERE {where}
        """
        all_params: list[str] = list(params)
        if limit > 0:
            sql += " LIMIT ?"
            all_params.append(str(limit))

        rows = self.conn.execute(sql, all_params).fetchall()
        return [dict(r) for r in rows]  # type: ignore[return-value]

    # -------------------------------------------------------------------
    # Sync bookkeeping
    # -------------------------------------------------------------------

    def is_stale(self, slug: str, hours: float) -> bool:
        """Check if a company needs re-syncing."""
        row = self.conn.execute(
            "SELECT last_sync FROM sync_status WHERE company_slug = ?",
            (slug,),
        ).fetchone()
        if not row:
            return True
        try:
            last = datetime.fromisoformat(row["last_sync"])
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            return elapsed > hours
        except (ValueError, TypeError):
            return True

    def record_sync(self, slug: str, count: int) -> None:
        """Record a successful sync."""
        now = _utcnow()
        self.conn.execute(
            """INSERT OR REPLACE INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (?, ?, ?, NULL)""",
            (slug, now, count),
        )
        self.conn.commit()

    def record_sync_error(self, slug: str, error: str) -> None:
        """Record a sync failure."""
        now = _utcnow()
        row = self.conn.execute(
            "SELECT job_count FROM sync_status WHERE company_slug = ?",
            (slug,),
        ).fetchone()
        job_count = row["job_count"] if row else 0
        self.conn.execute(
            """INSERT OR REPLACE INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (?, ?, ?, ?)""",
            (slug, now, job_count, error),
        )
        self.conn.commit()

    def get_sync_status(self, slug: str | None = None) -> list[dict]:
        """Get sync status for one or all companies."""
        if slug:
            rows = self.conn.execute(
                "SELECT * FROM sync_status WHERE company_slug = ?", (slug,)
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
