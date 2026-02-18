"""Sync pipeline — fetch, enrich, embed job listings.

Orchestrates three phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. EmbedPhase: per-model embedding generation
"""

from pathlib import Path

from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.store import JobStore
from jobbuddy.sync.types import SyncCallbacks, SyncResult
from jobbuddy.sync.embed import EmbedPhase
from jobbuddy.sync.enrich import EnrichPhase
from jobbuddy.sync.fetch import FetchPhase


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def sync_jobs(
    company_slug: str | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    callbacks: SyncCallbacks | None = None,
    db_path: Path | str | None = None,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the SQLite cache.

    Three phases: fetch → enrich → embed. Each phase is independent and
    communicates through the store.
    """
    import random

    cb = callbacks or SyncCallbacks()
    registry = list_companies()

    # Build target list (validate before opening DB)
    if company_slug:
        company = lookup_by_name(company_slug)
        if not company:
            raise ValueError(f"Unknown company: {company_slug}")
        if not company.ats:
            raise ValueError(f"No ATS configured for {company.name}")
        targets = [company]
    else:
        targets = [c for c in registry.values() if c.ats is not None]

    store = JobStore(db_path, thread_safe=True)

    try:
        # Filter by staleness
        if stale_hours is not None:
            filtered = []
            for c in targets:
                if store.is_stale(c.slug, stale_hours):
                    filtered.append(c)
                elif cb.on_skip:
                    cb.on_skip(c.slug, "recently synced")
            targets = filtered

        if not targets:
            return []

        # Shuffle to spread same-platform companies across time
        random.shuffle(targets)

        # Phase 1: Fetch
        results, slugs_to_embed = FetchPhase(store, targets, max_workers, cb).run()

        # Phase 2: Enrich descriptions for stub fetchers
        if slugs_to_embed:
            EnrichPhase(store, slugs_to_embed, targets, max_workers, cb).run()

        # Phase 3: Embed
        if slugs_to_embed:
            EmbedPhase(store, slugs_to_embed, cb).run()

        return results
    finally:
        store.close()
