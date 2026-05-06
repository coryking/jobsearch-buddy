"""One-shot backfill for jobs.published_at — paired with issue #40.

Run after deploying the issue-40 changes. Two phases, in this order:

1. Avature sitemap backfill. The pure-insert upsert won't write a
   sitemap-derived published_at to existing rows, so we fetch each
   Avature tenant's sitemap once and apply <lastmod> per row directly
   via update_enrichment.

2. last_seen::date fallback. Any row still NULL after step 1 (Tesla
   5.9k + small leakage in Workday/SF/Eightfold/Greenhouse) gets
   published_at = last_seen::date. last_seen is NOT NULL on every row,
   so this guarantees zero remaining NULLs.

After this runs cleanly, the NOT NULL migration on published_at can
ship without surprises.

Usage (from repo root):
    uv run python scripts/backfill_published_at.py [--dry-run]

Throwaway: delete after the backfill is in production.
"""

from __future__ import annotations

import argparse
import logging
import sys

from jobbuddy.fetchers import get_fetcher
from jobbuddy.fetchers.avature import AvatureFetcher
from jobbuddy.settings import pg_conninfo_with_token
from jobbuddy.store import JobStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill")


def _avature_companies_with_nulls(store: JobStore) -> list[str]:
    rows = store.conn.execute("""
        SELECT DISTINCT j.company_slug
        FROM jobs j
        JOIN companies c ON c.slug = j.company_slug
        WHERE c.ats = 'avature' AND j.published_at IS NULL
        ORDER BY j.company_slug
    """).fetchall()
    return [r["company_slug"] for r in rows]


def avature_sitemap_backfill(store: JobStore, *, dry_run: bool) -> int:
    """For each Avature tenant with NULL published_at rows, fetch the
    sitemap once and apply <lastmod> per row. Returns rows filled."""
    slugs = _avature_companies_with_nulls(store)
    if not slugs:
        log.info("[avature] no NULL rows; skipping")
        return 0

    total_filled = 0
    for slug in slugs:
        company = store.get_company(slug)
        if company is None:
            log.warning("[avature] %s: company missing from registry; skip", slug)
            continue

        fetcher = get_fetcher(company)
        if not isinstance(fetcher, AvatureFetcher):
            log.warning("[avature] %s: fetcher type %s, expected Avature", slug, type(fetcher).__name__)
            continue

        log.info("[avature] fetching sitemap for %s", slug)
        sitemap_dates = fetcher._fetch_sitemap_dates()
        if not sitemap_dates:
            log.warning("[avature] %s: empty sitemap; skipping", slug)
            continue

        null_jids = [
            r["job_id"]
            for r in store.conn.execute(
                "SELECT job_id FROM jobs WHERE company_slug = %s AND published_at IS NULL",
                (slug,),
            ).fetchall()
        ]
        payloads = {
            jid: {"published_at": sitemap_dates[jid]}
            for jid in null_jids
            if jid in sitemap_dates
        }

        log.info(
            "[avature] %s: %d NULL rows, %d covered by sitemap, %d uncovered",
            slug, len(null_jids), len(payloads), len(null_jids) - len(payloads),
        )
        if dry_run or not payloads:
            continue

        store.update_enrichment(slug, payloads)
        total_filled += len(payloads)

    return total_filled


def last_seen_fallback(store: JobStore, *, dry_run: bool) -> int:
    """Any row still NULL gets published_at = last_seen::date. Pure SQL."""
    remaining = store.conn.execute(
        "SELECT COUNT(*) AS cnt FROM jobs WHERE published_at IS NULL"
    ).fetchone()["cnt"]
    log.info("[fallback] %d rows remain NULL", remaining)
    if dry_run or remaining == 0:
        return 0

    with store.conn.transaction():
        cur = store.conn.execute(
            "UPDATE jobs SET published_at = last_seen::date "
            "WHERE published_at IS NULL"
        )
    log.info("[fallback] filled %d rows from last_seen::date", cur.rowcount)
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Read-only: print what would change, don't write.")
    args = parser.parse_args()

    store = JobStore(pg_conninfo_with_token())
    try:
        log.info("=== Phase 1: Avature sitemap ===")
        avature_filled = avature_sitemap_backfill(store, dry_run=args.dry_run)
        log.info("[avature] done. %d rows filled.", avature_filled)

        log.info("=== Phase 2: last_seen::date fallback ===")
        fallback_filled = last_seen_fallback(store, dry_run=args.dry_run)
        log.info("[fallback] done. %d rows filled.", fallback_filled)

        if not args.dry_run:
            null_remaining = store.conn.execute(
                "SELECT COUNT(*) AS cnt FROM jobs WHERE published_at IS NULL"
            ).fetchone()["cnt"]
            log.info("Final NULL count: %d", null_remaining)
            if null_remaining > 0:
                log.warning("Some rows still NULL — investigate before NOT NULL migration.")
                return 1
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
