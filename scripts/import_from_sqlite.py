#!/usr/bin/env python3
"""Import all job data from SQLite into PostgreSQL.

Truncates jobs + sync_status (NOT activity_log), then imports everything
from SQLite in batches: jobs with description_stripped, sync_status, and
embeddings. Prints progress throughout.

Usage:
    python scripts/import_from_sqlite.py
    python scripts/import_from_sqlite.py --sqlite-path /path/to/jobs_cache.db
"""

import argparse
import sqlite3
import struct

import psycopg
from pgvector.psycopg import register_vector
from platformdirs import user_data_dir

CHUNK = 1000


def deserialize_f32(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def run(sqlite_path: str, pg_conninfo: str) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    print(f"SQLite: {sqlite_path}", flush=True)

    dst = psycopg.connect(pg_conninfo, autocommit=True)
    register_vector(dst)

    # Ensure schema exists
    from jobbuddy.store import JobStore
    store = JobStore(pg_conninfo)
    store.close()

    # Wipe jobs + sync_status only (CASCADE handles FK ordering)
    print("Truncating jobs + sync_status...", flush=True)
    dst.execute("TRUNCATE jobs, sync_status CASCADE")
    dst.execute("ALTER SEQUENCE jobs_id_seq RESTART WITH 1")

    # --- Sync status first (jobs FK references sync_status) ---
    sync_rows = src.execute("SELECT * FROM sync_status").fetchall()
    print(f"Importing {len(sync_rows)} sync_status rows...", flush=True)
    with dst.transaction():
        cur = dst.cursor()
        cur.executemany(
            """INSERT INTO sync_status (company_slug, last_sync, job_count, error)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (company_slug) DO NOTHING""",
            [(r["company_slug"], r["last_sync"], r["job_count"], r.get("error"))
             for r in sync_rows],
        )

    # --- Jobs (with description_stripped) ---
    total_jobs = src.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"Importing {total_jobs} jobs...", flush=True)

    imported = 0
    offset = 0
    while True:
        rows = src.execute(
            "SELECT * FROM jobs LIMIT ? OFFSET ?", (CHUNK, offset)
        ).fetchall()
        if not rows:
            break

        params = []
        for row in rows:
            r = dict(row)
            # Map empty description_stripped to NULL (PG CHECK rejects '')
            stripped = r.get("description_stripped") or None
            params.append((
                r["company_slug"], r["job_id"], r["title"],
                r.get("location"), r.get("url"), r.get("published_at"),
                r.get("department"), r.get("team"), r.get("salary"),
                r.get("description"), stripped,
                r.get("ats_metadata"), r["last_seen"], r.get("disappeared_at"),
            ))

        with dst.transaction():
            cur = dst.cursor()
            cur.executemany(
                """INSERT INTO jobs
                   (company_slug, job_id, title, location, url, published_at,
                    department, team, salary, description, description_stripped,
                    ats_metadata, last_seen, disappeared_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (company_slug, job_id) DO NOTHING""",
                params,
            )

        imported += len(rows)
        offset += CHUNK
        print(f"  jobs: {imported}/{total_jobs}", flush=True)

    # --- Embeddings ---
    emb_table = src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_embeddings'"
    ).fetchone()

    if emb_table:
        total_emb = src.execute(
            "SELECT COUNT(*) FROM job_embeddings je JOIN jobs j ON j.id = je.job_id"
        ).fetchone()[0]
        print(f"Importing {total_emb} embeddings...", flush=True)

        emb_done = 0
        offset = 0
        while True:
            rows = src.execute(
                """SELECT je.embedding, j.company_slug, j.job_id AS ats_job_id
                   FROM job_embeddings je
                   JOIN jobs j ON j.id = je.job_id
                   LIMIT ? OFFSET ?""",
                (CHUNK, offset),
            ).fetchall()
            if not rows:
                break

            batch = [(deserialize_f32(r["embedding"]), r["company_slug"], r["ats_job_id"])
                     for r in rows]

            with dst.transaction():
                cur = dst.cursor()
                cur.executemany(
                    "UPDATE jobs SET embedding = %s WHERE company_slug = %s AND job_id = %s",
                    batch,
                )

            emb_done += len(batch)
            offset += CHUNK
            print(f"  embeddings: {emb_done}/{total_emb}", flush=True)

    # --- Verify ---
    counts = dst.execute("""
        SELECT COUNT(*) AS jobs,
               COUNT(*) FILTER (WHERE description_stripped IS NOT NULL) AS stripped,
               COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
        FROM jobs
    """).fetchone()
    sync_count = dst.execute("SELECT COUNT(*) FROM sync_status").fetchone()[0]

    print(f"\nDone. PG now has:", flush=True)
    print(f"  Jobs:     {counts[0]}", flush=True)
    print(f"  Stripped: {counts[1]}", flush=True)
    print(f"  Embedded: {counts[2]}", flush=True)
    print(f"  Sync:     {sync_count}", flush=True)

    src.close()
    dst.close()


def main():
    default_sqlite = str(
        __import__("pathlib").Path(user_data_dir("jobsearch-buddy")) / "jobs_cache.db"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", default=default_sqlite)
    parser.add_argument("--pg-service", default="job-search-buddy-remote")
    args = parser.parse_args()
    run(args.sqlite_path, f"service={args.pg_service}")


if __name__ == "__main__":
    main()
