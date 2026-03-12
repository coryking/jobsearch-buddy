#!/usr/bin/env python3
"""Migrate job data from SQLite to PostgreSQL.

Reads all data from the existing SQLite DB and writes it to PostgreSQL.
Idempotent (uses ON CONFLICT).

Usage:
    python scripts/migrate_sqlite_to_pg.py
    python scripts/migrate_sqlite_to_pg.py --sqlite-path /path/to/jobs_cache.db
    python scripts/migrate_sqlite_to_pg.py --pg-service job-search-buddy-remote
"""

import argparse
import sqlite3
import struct
import sys

import psycopg
from pgvector.psycopg import register_vector
from platformdirs import user_data_dir


def deserialize_f32(blob: bytes) -> list[float]:
    """Convert SQLite BLOB bytes back to list of floats."""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def migrate(sqlite_path: str, pg_conninfo: str) -> None:
    # Connect to SQLite
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    print(f"Connected to SQLite: {sqlite_path}")

    # Connect to PostgreSQL
    dst = psycopg.connect(pg_conninfo, autocommit=True)
    register_vector(dst)
    print(f"Connected to PostgreSQL: {pg_conninfo}")

    # Ensure schema exists
    from jobbuddy.store import JobStore
    store = JobStore(pg_conninfo)
    store.close()

    # Migrate jobs
    jobs = src.execute("SELECT * FROM jobs").fetchall()
    print(f"Migrating {len(jobs)} jobs...")

    migrated = 0
    for row in jobs:
        r = dict(row)
        dst.execute(
            """INSERT INTO jobs
               (company_slug, job_id, title, location, url, published_at,
                department, team, salary, description, description_stripped,
                ats_metadata, last_seen, disappeared_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(company_slug, job_id) DO UPDATE SET
                title = excluded.title,
                location = excluded.location,
                url = excluded.url,
                published_at = excluded.published_at,
                department = excluded.department,
                team = excluded.team,
                salary = excluded.salary,
                description = COALESCE(excluded.description, jobs.description),
                description_stripped = COALESCE(excluded.description_stripped, jobs.description_stripped),
                ats_metadata = COALESCE(excluded.ats_metadata, jobs.ats_metadata),
                last_seen = excluded.last_seen,
                disappeared_at = excluded.disappeared_at""",
            (
                r["company_slug"],
                r["job_id"],
                r["title"],
                r.get("location"),
                r.get("url"),
                r.get("published_at"),
                r.get("department"),
                r.get("team"),
                r.get("salary"),
                r.get("description"),
                r.get("description_stripped"),
                r.get("ats_metadata"),  # TEXT in SQLite, JSONB in PG -- psycopg handles it
                r["last_seen"],
                r.get("disappeared_at"),
            ),
        )
        migrated += 1
    print(f"  Migrated {migrated} jobs")

    # Migrate sync_status
    sync_rows = src.execute("SELECT * FROM sync_status").fetchall()
    print(f"Migrating {len(sync_rows)} sync_status rows...")

    for row in sync_rows:
        r = dict(row)
        dst.execute(
            """INSERT INTO sync_status (company_slug, last_sync, job_count, error)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(company_slug) DO UPDATE SET
                last_sync = excluded.last_sync,
                job_count = excluded.job_count,
                error = excluded.error""",
            (r["company_slug"], r["last_sync"], r["job_count"], r.get("error")),
        )
    print(f"  Migrated {len(sync_rows)} sync_status rows")

    # Migrate embeddings
    emb_table = src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_embeddings'"
    ).fetchone()

    if emb_table:
        emb_rows = src.execute("""
            SELECT je.job_id, je.embedding, j.company_slug, j.job_id AS ats_job_id
            FROM job_embeddings je
            JOIN jobs j ON j.id = je.job_id
        """).fetchall()
        print(f"Migrating {len(emb_rows)} embeddings...")

        emb_migrated = 0
        for row in emb_rows:
            r = dict(row)
            vec = deserialize_f32(r["embedding"])

            # Find the PG job id by company_slug + job_id
            pg_row = dst.execute(
                "SELECT id FROM jobs WHERE company_slug = %s AND job_id = %s",
                (r["company_slug"], r["ats_job_id"]),
            ).fetchone()
            if pg_row:
                dst.execute(
                    "UPDATE jobs SET embedding = %s WHERE id = %s",
                    (vec, pg_row[0]),
                )
                emb_migrated += 1
        print(f"  Migrated {emb_migrated} embeddings")
    else:
        print("  No job_embeddings table found, skipping embeddings")

    src.close()
    dst.close()
    print("Migration complete.")


def main():
    default_sqlite = str(
        __import__("pathlib").Path(user_data_dir("jobsearch-buddy")) / "jobs_cache.db"
    )

    parser = argparse.ArgumentParser(description="Migrate SQLite job data to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        default=default_sqlite,
        help=f"Path to SQLite DB (default: {default_sqlite})",
    )
    parser.add_argument(
        "--pg-service",
        default="job-search-buddy-remote",
        help="pg_service.conf service name (default: job-search-buddy-remote)",
    )
    args = parser.parse_args()

    pg_conninfo = f"service={args.pg_service}"
    migrate(args.sqlite_path, pg_conninfo)


if __name__ == "__main__":
    main()
