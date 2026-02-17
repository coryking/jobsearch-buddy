"""SQLite cache for job listings — enables cross-company offline search and semantic search.

The cache stores discovery metadata (title, location, salary, team, description) for all
synced companies. Descriptions that arrive via list_jobs() are stored and embedded for
vector search via sqlite-vec. All search/browse reads come from here; only `ats sync`
touches the network.

Schema: jobs (company_slug, job_id PK) + sync_status (company_slug PK) +
        vec_jobs (sqlite-vec virtual table) + job_embeddings (rowid mapping).
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec

from jobbuddy import embeddings
from jobbuddy.models import Job

log = logging.getLogger(__name__)

def _default_db_path() -> Path:
    from jobbuddy.settings import get_settings
    return get_settings().db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company_slug   TEXT NOT NULL,
    job_id         TEXT NOT NULL,
    title          TEXT NOT NULL,
    location       TEXT,
    url            TEXT,
    published_at   DATE,
    department     TEXT,
    team           TEXT,
    salary         TEXT,
    last_seen      TIMESTAMP NOT NULL,
    disappeared_at TIMESTAMP,
    PRIMARY KEY (company_slug, job_id)
);

CREATE TABLE IF NOT EXISTS sync_status (
    company_slug TEXT PRIMARY KEY,
    last_sync    TIMESTAMP NOT NULL,
    job_count    INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);
"""

_MIGRATIONS = [
    # Add disappeared_at column (soft-delete for jobs no longer in the feed)
    "ALTER TABLE jobs ADD COLUMN disappeared_at TIMESTAMP",
    # Store descriptions for embedding/vector search
    "ALTER TABLE jobs ADD COLUMN description TEXT",
    # ATS-specific metadata as JSON (e.g. Workday ext_path, Eightfold positionUrl)
    "ALTER TABLE jobs ADD COLUMN ats_metadata TEXT",
]


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


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode, Row factory, and sqlite-vec."""
    resolved = Path(db_path or _default_db_path())
    resolved.parent.mkdir(parents=True, exist_ok=True)
    path = str(resolved)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _run_migrations(conn)

    # Load sqlite-vec extension and create vector tables
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _init_vec_tables(conn)

    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema migrations idempotently (column-existence checks)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for sql in _MIGRATIONS:
        # Extract the column name from ALTER TABLE ... ADD COLUMN <name> ...
        # Only run if the column doesn't already exist
        parts = sql.upper().split("ADD COLUMN")
        if len(parts) == 2:
            col_name = parts[1].strip().split()[0].lower()
            if col_name in cols:
                continue
        conn.execute(sql)
    conn.commit()


def _init_vec_tables(conn: sqlite3.Connection) -> None:
    """Create sqlite-vec virtual table and rowid mapping table if they don't exist."""
    # vec0 virtual table for KNN search
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_jobs USING vec0(embedding float[{embeddings.DIMENSIONS}])"
    )
    # Mapping table: vec0 requires integer rowids, but jobs has composite PK
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_embeddings (
            rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
            company_slug TEXT NOT NULL,
            job_id       TEXT NOT NULL,
            UNIQUE (company_slug, job_id)
        )
    """)
    # Migration: add text_hash column for skip-if-unchanged logic
    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_embeddings)").fetchall()}
    if "text_hash" not in cols:
        conn.execute("ALTER TABLE job_embeddings ADD COLUMN text_hash TEXT")
    conn.commit()


def upsert_company_jobs(
    conn: sqlite3.Connection, company_slug: str, jobs: list[Job]
) -> None:
    """Sync jobs for a company: upsert current listings, soft-delete stale ones.

    Deduplicates input by job_id (last wins), upserts all current jobs via
    INSERT OR REPLACE, and marks jobs no longer in the feed with a
    disappeared_at timestamp. Jobs that reappear get their disappeared_at cleared.
    """
    now = _utcnow()

    # Deduplicate by job_id (last occurrence wins)
    by_id: dict[str, Job] = {}
    for j in jobs:
        by_id[j.id] = j
    jobs = list(by_id.values())

    with conn:
        # What's currently cached (active) for this company?
        current_ids = {
            row["job_id"]
            for row in conn.execute(
                "SELECT job_id FROM jobs WHERE company_slug = ?", (company_slug,)
            ).fetchall()
        }
        new_ids = {j.id for j in jobs}

        # Mark jobs no longer in the feed as disappeared
        gone = current_ids - new_ids
        if gone:
            conn.executemany(
                """UPDATE jobs SET disappeared_at = ?
                   WHERE company_slug = ? AND job_id = ? AND disappeared_at IS NULL""",
                [(now, company_slug, jid) for jid in gone],
            )

        # Upsert all current listings (clears disappeared_at if a job reappears).
        # COALESCE keeps existing description when incoming value is NULL —
        # this preserves descriptions enriched by fetch_descriptions() across re-syncs.
        conn.executemany(
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
                    company_slug,
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

        conn.execute(
            """INSERT OR REPLACE INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (?, ?, ?, NULL)""",
            (company_slug, now, len(jobs)),
        )


def record_sync_error(
    conn: sqlite3.Connection, company_slug: str, error: str
) -> None:
    """Record a sync failure for a company."""
    now = _utcnow()
    with conn:
        # Preserve existing job_count if any
        row = conn.execute(
            "SELECT job_count FROM sync_status WHERE company_slug = ?",
            (company_slug,),
        ).fetchone()
        job_count = row["job_count"] if row else 0
        conn.execute(
            """INSERT OR REPLACE INTO sync_status
               (company_slug, last_sync, job_count, error)
               VALUES (?, ?, ?, ?)""",
            (company_slug, now, job_count, error),
        )


def query_jobs(
    conn: sqlite3.Connection,
    *,
    company_slug: str | None = None,
    title_filter: str | None = None,
    location_filter: str | None = None,
    include_disappeared: bool = False,
    limit: int = 100,
) -> list[dict]:
    """Query cached jobs with optional LIKE filters.

    Filters use case-insensitive substring matching. Comma-separated values
    in title_filter/location_filter produce OR conditions.
    By default, excludes jobs that have disappeared from the feed.
    Returns dicts with job fields + company_slug + last_sync from sync_status.
    """
    conditions = []
    params: list[str] = []

    if not include_disappeared:
        conditions.append("j.disappeared_at IS NULL")

    if company_slug:
        conditions.append("j.company_slug = ?")
        params.append(company_slug)

    if title_filter:
        terms = [t.strip() for t in title_filter.split(",") if t.strip()]
        if terms:
            or_clauses = ["j.title LIKE ? COLLATE NOCASE"] * len(terms)
            conditions.append(f"({' OR '.join(or_clauses)})")
            params.extend(f"%{t}%" for t in terms)

    if location_filter:
        terms = [t.strip() for t in location_filter.split(",") if t.strip()]
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

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_sync_status(
    conn: sqlite3.Connection, company_slug: str | None = None
) -> list[dict]:
    """Get sync status for one or all companies."""
    if company_slug:
        rows = conn.execute(
            "SELECT * FROM sync_status WHERE company_slug = ?", (company_slug,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sync_status ORDER BY last_sync DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def is_stale(conn: sqlite3.Connection, company_slug: str, hours: float) -> bool:
    """Check if a company needs re-syncing (last_sync older than `hours` ago, or never synced)."""
    row = conn.execute(
        "SELECT last_sync FROM sync_status WHERE company_slug = ?",
        (company_slug,),
    ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row["last_sync"])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return elapsed > hours
    except (ValueError, TypeError):
        return True


def cache_exists(db_path: Path | str | None = None) -> bool:
    """Check if the cache database file exists."""
    path = Path(db_path or _default_db_path())
    return path.exists()


def job_count(conn: sqlite3.Connection, include_disappeared: bool = False) -> int:
    """Total number of cached jobs."""
    sql = "SELECT COUNT(*) as cnt FROM jobs"
    if not include_disappeared:
        sql += " WHERE disappeared_at IS NULL"
    row = conn.execute(sql).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Description enrichment
# ---------------------------------------------------------------------------


def get_jobs_needing_descriptions(
    conn: sqlite3.Connection, company_slug: str
) -> list[dict]:
    """Return active jobs with NULL description for a company (includes ats_metadata)."""
    rows = conn.execute(
        """SELECT job_id, title, ats_metadata FROM jobs
           WHERE company_slug = ? AND description IS NULL AND disappeared_at IS NULL""",
        (company_slug,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_job_descriptions(
    conn: sqlite3.Connection, company_slug: str, descriptions: dict[str, str]
) -> None:
    """Batch update description column for jobs in a company."""
    if not descriptions:
        return
    with conn:
        conn.executemany(
            "UPDATE jobs SET description = ? WHERE company_slug = ? AND job_id = ?",
            [(desc, company_slug, job_id) for job_id, desc in descriptions.items()],
        )


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------


def count_pending_embeddings(conn: sqlite3.Connection, company_slug: str | None = None) -> int:
    """Count jobs that need embeddings (have descriptions but no embedding yet)."""
    conditions = ["j.description IS NOT NULL"]
    params: list[str] = []
    if company_slug:
        conditions.append("j.company_slug = ?")
        params.append(company_slug)
    where = " AND ".join(conditions)
    row = conn.execute(
        f"""SELECT COUNT(*) FROM jobs j
            LEFT JOIN job_embeddings e ON j.company_slug = e.company_slug AND j.job_id = e.job_id
            WHERE {where} AND e.rowid IS NULL""",
        params,
    ).fetchone()
    return row[0]


def _text_hash(text: str) -> str:
    """SHA-256 hex digest of embedding text, used to skip unchanged descriptions."""
    return hashlib.sha256(text.encode()).hexdigest()


def generate_embeddings(
    conn: sqlite3.Connection,
    company_slug: str | None = None,
    on_progress: "callable | None" = None,
) -> int:
    """Generate embeddings for jobs with descriptions, skipping unchanged ones.

    Uses a content hash on the embed text to detect changes. Jobs whose
    descriptions changed get their old embeddings replaced. Returns count of
    new/updated embeddings.

    Args:
        on_progress: Optional callback(done, total) called after each embedding is stored.
    """
    # Find all jobs with descriptions, including those already embedded
    conditions = ["j.description IS NOT NULL"]
    params: list[str] = []
    if company_slug:
        conditions.append("j.company_slug = ?")
        params.append(company_slug)

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"""SELECT j.company_slug, j.job_id, j.title, j.department, j.location, j.description,
                   e.rowid AS embed_rowid, e.text_hash
            FROM jobs j
            LEFT JOIN job_embeddings e ON j.company_slug = e.company_slug AND j.job_id = e.job_id
            WHERE {where}""",
        params,
    ).fetchall()

    if not rows:
        return 0

    # Build embed texts, skip jobs where hash matches (unchanged)
    # (company_slug, job_id, text, hash, old_embed_rowid_or_None)
    jobs_to_embed: list[tuple[str, str, str, str, int | None]] = []
    for row in rows:
        job = Job(
            id=row["job_id"],
            title=row["title"],
            location=row["location"] or "",
            url="",
            apply_url="",
            department=row["department"],
            description=row["description"],
        )
        text = job.embed_text(row["company_slug"])
        if not text:
            continue
        h = _text_hash(text)
        if row["embed_rowid"] is not None and row["text_hash"] == h:
            continue  # unchanged — skip
        jobs_to_embed.append((
            row["company_slug"], row["job_id"], text, h, row["embed_rowid"],
        ))

    if not jobs_to_embed:
        return 0

    # Stream embeddings one at a time so progress updates between fastembed batches
    # and Ctrl+C can interrupt between batches instead of blocking in ONNX.
    texts = [t[2] for t in jobs_to_embed]
    done = 0
    for (slug, job_id, _, h, old_rowid), vec in zip(jobs_to_embed, embeddings.embed_texts_iter(texts)):
        # If re-embedding (description changed), delete old rows first
        if old_rowid is not None:
            conn.execute("DELETE FROM vec_jobs WHERE rowid = ?", (old_rowid,))
            conn.execute("DELETE FROM job_embeddings WHERE rowid = ?", (old_rowid,))

        cursor = conn.execute(
            "INSERT INTO job_embeddings (company_slug, job_id, text_hash) VALUES (?, ?, ?)",
            (slug, job_id, h),
        )
        rowid = cursor.lastrowid
        conn.execute(
            "INSERT INTO vec_jobs (rowid, embedding) VALUES (?, ?)",
            (rowid, embeddings.serialize_f32(vec)),
        )
        conn.commit()
        done += 1
        if on_progress:
            on_progress(done, len(jobs_to_embed))

    return len(jobs_to_embed)


def semantic_search(
    conn: sqlite3.Connection,
    query_embedding: bytes,
    limit: int = 20,
) -> list[dict]:
    """KNN search over job embeddings. Returns job metadata + distance score.

    query_embedding should be serialized via embeddings.serialize_f32().
    Excludes disappeared jobs.
    """
    rows = conn.execute(
        """SELECT j.*, s.last_sync, v.distance
           FROM vec_jobs v
           JOIN job_embeddings e ON e.rowid = v.rowid
           JOIN jobs j ON j.company_slug = e.company_slug AND j.job_id = e.job_id
           LEFT JOIN sync_status s ON j.company_slug = s.company_slug
           WHERE v.embedding MATCH ? AND k = ?
             AND j.disappeared_at IS NULL
           ORDER BY v.distance""",
        (query_embedding, limit),
    ).fetchall()
    return [dict(row) for row in rows]
