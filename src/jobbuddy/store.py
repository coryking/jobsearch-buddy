"""JobStore — SQLite persistence layer for job listings and embeddings.

Replaces cache.py's loose functions with a class that owns the connection,
manages schema migrations, and provides thread-safe write operations.

Schema uses a surrogate INTEGER PRIMARY KEY on jobs with a UNIQUE constraint
on (company_slug, job_id). Embeddings stored as BLOBs in job_embeddings,
one row per (job, model).
"""

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

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
        thread_safe: If True, wraps writes in a threading.Lock and uses
            check_same_thread=False. Use for sync (enrichment threads).
    """

    def __init__(self, db_path: Path | str | None = None, *, thread_safe: bool = False):
        self._lock = threading.Lock() if thread_safe else None
        self.conn = self._connect(db_path, thread_safe=thread_safe)

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------------------------
    # Connection + Schema
    # -------------------------------------------------------------------

    def _connect(self, db_path: Path | str | None, *, thread_safe: bool = False) -> sqlite3.Connection:
        if db_path is None:
            from jobbuddy.settings import get_settings
            db_path = get_settings().db_path

        resolved = Path(db_path) if str(db_path) != ":memory:" else db_path
        if isinstance(resolved, Path):
            resolved.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=not thread_safe,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        self._ensure_schema(conn)
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

        # Drop old sqlite-vec tables if they exist
        for old_table in ("vec_jobs",):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {old_table}")
            except sqlite3.OperationalError:
                pass  # virtual table might not exist

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
        ]
        for col_name, col_type in migrations:
            if col_name not in cols:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
        conn.commit()

    def _init_embedding_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS embedding_models (
                model_key   TEXT PRIMARY KEY,
                model_name  TEXT NOT NULL,
                dimensions  INTEGER NOT NULL,
                created_at  TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_embeddings (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id    INTEGER NOT NULL REFERENCES jobs(id),
                model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
                text_hash TEXT NOT NULL,
                embedding BLOB NOT NULL,
                UNIQUE (job_id, model_key)
            );
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

        def _do():
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

        if self._lock:
            with self._lock:
                _do()
        else:
            _do()

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

        def _do():
            with self.conn:
                self.conn.executemany(
                    "UPDATE jobs SET description = ? WHERE company_slug = ? AND job_id = ?",
                    [(desc, slug, job_id) for job_id, desc in descs.items()],
                )

        if self._lock:
            with self._lock:
                _do()
        else:
            _do()

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

    def _ensure_model_registered(self, model_key: str) -> None:
        """Register model in embedding_models if not already present."""
        from jobbuddy.embeddings import get_config
        config = get_config(model_key)
        self.conn.execute(
            """INSERT OR IGNORE INTO embedding_models (model_key, model_name, dimensions, created_at)
               VALUES (?, ?, ?, ?)""",
            (config.model_key, config.model_name, config.dimensions, _utcnow()),
        )

    def jobs_needing_embeddings(
        self,
        model_key: str,
        slug: str | None = None,
        *,
        count_only: bool = False,
    ) -> int | list[dict]:
        """Jobs that need embeddings: have descriptions but no up-to-date embedding.

        This is the single source of truth for both counting and iterating,
        ensuring consistency (fixes smell #4).

        A job needs embedding if:
        - It has a description AND
        - It has no embedding for this model_key, OR
        - Its text_hash doesn't match the current embed_text hash
        """
        conditions = ["j.description IS NOT NULL", "j.disappeared_at IS NULL"]
        params: list[str] = []

        if slug:
            conditions.append("j.company_slug = ?")
            params.append(slug)

        where = " AND ".join(conditions)

        if count_only:
            # Count jobs where either no embedding exists or hash doesn't match
            sql = f"""
                SELECT COUNT(*) FROM jobs j
                LEFT JOIN job_embeddings e ON j.id = e.job_id AND e.model_key = ?
                WHERE {where} AND (e.id IS NULL OR e.text_hash != j.description)
            """
            # We use j.description as a proxy — actual hash check happens at embed time.
            # But for count, we check if embedding exists at all first.
            sql = f"""
                SELECT COUNT(*) FROM jobs j
                LEFT JOIN job_embeddings e ON j.id = e.job_id AND e.model_key = ?
                WHERE {where} AND e.id IS NULL
            """
            row = self.conn.execute(sql, [model_key] + params).fetchone()
            no_embedding = row[0]

            # Also count hash mismatches (description changed)
            sql2 = f"""
                SELECT COUNT(*) FROM jobs j
                JOIN job_embeddings e ON j.id = e.job_id AND e.model_key = ?
                WHERE {where}
            """
            row2 = self.conn.execute(sql2, [model_key] + params).fetchone()
            has_embedding = row2[0]

            if has_embedding == 0:
                return no_embedding

            # Check hash mismatches by loading them
            sql3 = f"""
                SELECT j.id, j.company_slug, j.job_id, j.title, j.department,
                       j.location, j.description, e.text_hash
                FROM jobs j
                JOIN job_embeddings e ON j.id = e.job_id AND e.model_key = ?
                WHERE {where}
            """
            rows = self.conn.execute(sql3, [model_key] + params).fetchall()
            mismatches = 0
            for r in rows:
                job = Job(
                    id=r["job_id"], title=r["title"],
                    location=r["location"] or "", url="", apply_url="",
                    department=r["department"], description=r["description"],
                )
                text = job.embed_text(r["company_slug"])
                if text and _text_hash(text) != r["text_hash"]:
                    mismatches += 1

            return no_embedding + mismatches

        # List mode — return full job info for embedding
        sql = f"""
            SELECT j.id, j.company_slug, j.job_id, j.title, j.department,
                   j.location, j.description, e.id AS embed_id, e.text_hash
            FROM jobs j
            LEFT JOIN job_embeddings e ON j.id = e.job_id AND e.model_key = ?
            WHERE {where}
        """
        rows = self.conn.execute(sql, [model_key] + params).fetchall()

        result = []
        for r in rows:
            job = Job(
                id=r["job_id"], title=r["title"],
                location=r["location"] or "", url="", apply_url="",
                department=r["department"], description=r["description"],
            )
            text = job.embed_text(r["company_slug"])
            if not text:
                continue

            h = _text_hash(text)
            if r["embed_id"] is not None and r["text_hash"] == h:
                continue  # up to date

            result.append({
                "id": r["id"],
                "company_slug": r["company_slug"],
                "job_id": r["job_id"],
                "text": text,
                "text_hash": h,
                "old_embed_id": r["embed_id"],
            })

        return result

    def store_embedding(self, job_id: int, model_key: str, embedding: bytes, text_hash: str) -> None:
        """Store or replace an embedding for a job+model pair."""

        def _do():
            self._ensure_model_registered(model_key)
            self.conn.execute(
                """INSERT INTO job_embeddings (job_id, model_key, text_hash, embedding)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, model_key) DO UPDATE SET
                    text_hash = excluded.text_hash,
                    embedding = excluded.embedding""",
                (job_id, model_key, text_hash, embedding),
            )
            self.conn.commit()

        if self._lock:
            with self._lock:
                _do()
        else:
            _do()

    def delete_embedding(self, job_id: int, model_key: str) -> None:
        """Delete an embedding for a job+model pair."""

        def _do():
            self.conn.execute(
                "DELETE FROM job_embeddings WHERE job_id = ? AND model_key = ?",
                (job_id, model_key),
            )
            self.conn.commit()

        if self._lock:
            with self._lock:
                _do()
        else:
            _do()

    def load_embeddings(self, model_key: str) -> tuple[np.ndarray, list[int]]:
        """Load all embeddings for a model as (numpy matrix, list of job_ids).

        Returns empty arrays if no embeddings exist.
        """
        from jobbuddy.embeddings import get_config
        config = get_config(model_key)

        rows = self.conn.execute(
            "SELECT job_id, embedding FROM job_embeddings WHERE model_key = ?",
            (model_key,),
        ).fetchall()

        if not rows:
            return np.empty((0, config.dimensions), dtype=np.float32), []

        job_ids = []
        vectors = []
        for row in rows:
            job_ids.append(row["job_id"])
            vec = np.frombuffer(row["embedding"], dtype=np.float32).copy()
            vectors.append(vec)

        return np.vstack(vectors), job_ids

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
