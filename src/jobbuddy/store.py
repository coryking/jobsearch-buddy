"""JobStore — SQLite persistence layer for job listings and embeddings.

Schema uses a surrogate INTEGER PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id). Embeddings stored as BLOBs in job_embeddings,
with a vec0 virtual table (sqlite-vec) for cosine similarity search.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import sqlite_vec

from jobbuddy.models import Job

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


def _text_hash(text: str) -> str:
    """SHA-256 hex digest of embedding text."""
    return hashlib.sha256(text.encode()).hexdigest()


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

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS job_embeddings (
                job_id    INTEGER PRIMARY KEY REFERENCES jobs(id),
                text_hash TEXT NOT NULL,
                embedding BLOB NOT NULL
            );
        """)

        # vec0 virtual table for cosine similarity search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_jobs USING vec0(
                job_id INTEGER PRIMARY KEY,
                embedding float[1536] distance_metric=cosine
            )
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

    def get_jobs_needing_stripping(self, limit: int = 50, *, slug: str | None = None, count_only: bool = False) -> int | list[dict]:
        """Return active jobs with descriptions but no stripped version.

        With count_only=True, returns just the count (no LIMIT applied).
        """
        conditions = [
            "description IS NOT NULL",
            "description_stripped IS NULL",
            "disappeared_at IS NULL",
        ]
        params: list = []

        if slug:
            conditions.append("company_slug = ?")
            params.append(slug)

        where = " AND ".join(conditions)

        if count_only:
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE {where}", params
            ).fetchone()
            return row[0]

        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT id, company_slug, job_id, title, description FROM jobs
               WHERE {where}
               LIMIT ?""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def update_stripped_description(self, job_pk: int, stripped: str) -> None:
        """Set the stripped description for a job."""
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET description_stripped = ? WHERE id = ?",
                (stripped, job_pk),
            )

    def clear_stripped_descriptions(self) -> int:
        """Set description_stripped to NULL for all jobs. Returns count affected."""
        with self.conn:
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

    def store_embedding(self, job_id: int, embedding: bytes, text_hash: str) -> None:
        """Dual-write: job_embeddings (audit/hash) + vec_jobs (search index)."""
        self.conn.execute("""
            INSERT INTO job_embeddings (job_id, text_hash, embedding)
            VALUES (?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                text_hash = excluded.text_hash, embedding = excluded.embedding
        """, (job_id, text_hash, embedding))
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

    def jobs_needing_embeddings(
        self, slug: str | None = None, *, count_only: bool = False, limit: int = 0
    ) -> int | list[dict]:
        """Jobs with description_stripped but no up-to-date embedding.

        Gate on description_stripped IS NOT NULL (not description).
        """
        conditions = ["j.description_stripped IS NOT NULL", "j.disappeared_at IS NULL"]
        params: list[str] = []

        if slug:
            conditions.append("j.company_slug = ?")
            params.append(slug)

        where = " AND ".join(conditions)

        if count_only:
            # Count jobs where no embedding exists
            sql = f"""
                SELECT COUNT(*) FROM jobs j
                LEFT JOIN job_embeddings e ON j.id = e.job_id
                WHERE {where} AND e.job_id IS NULL
            """
            row = self.conn.execute(sql, params).fetchone()
            no_embedding = row[0]

            # Also count hash mismatches (description changed)
            sql2 = f"""
                SELECT COUNT(*) FROM jobs j
                JOIN job_embeddings e ON j.id = e.job_id
                WHERE {where}
            """
            row2 = self.conn.execute(sql2, params).fetchone()
            has_embedding = row2[0]

            if has_embedding == 0:
                return no_embedding

            # Check hash mismatches by loading them
            sql3 = f"""
                SELECT j.id, j.company_slug, j.job_id, j.title, j.department,
                       j.location, j.description, j.description_stripped, e.text_hash
                FROM jobs j
                JOIN job_embeddings e ON j.id = e.job_id
                WHERE {where}
            """
            rows = self.conn.execute(sql3, params).fetchall()
            mismatches = 0
            for r in rows:
                job = Job(
                    id=r["job_id"], title=r["title"],
                    location=r["location"] or "", url="", apply_url="",
                    department=r["department"], description=r["description"],
                )
                text = job.embed_text(r["company_slug"], description_stripped=r["description_stripped"])
                if text and _text_hash(text) != r["text_hash"]:
                    mismatches += 1

            return no_embedding + mismatches

        # List mode — return full job info for embedding
        sql = f"""
            SELECT j.id, j.company_slug, j.job_id, j.title, j.department,
                   j.location, j.description, j.description_stripped,
                   e.job_id AS has_embedding, e.text_hash
            FROM jobs j
            LEFT JOIN job_embeddings e ON j.id = e.job_id
            WHERE {where}
        """
        all_params = list(params)
        if limit > 0:
            sql += " LIMIT ?"
            all_params.append(str(limit))

        rows = self.conn.execute(sql, all_params).fetchall()

        result = []
        for r in rows:
            job = Job(
                id=r["job_id"], title=r["title"],
                location=r["location"] or "", url="", apply_url="",
                department=r["department"], description=r["description"],
            )
            text = job.embed_text(r["company_slug"], description_stripped=r["description_stripped"])
            if not text:
                continue

            h = _text_hash(text)
            if r["has_embedding"] is not None and r["text_hash"] == h:
                continue  # up to date

            result.append({
                "id": r["id"],
                "company_slug": r["company_slug"],
                "job_id": r["job_id"],
                "text": text,
                "text_hash": h,
                "has_embedding": r["has_embedding"] is not None,
            })

        return result

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
