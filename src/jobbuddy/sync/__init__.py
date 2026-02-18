"""Sync pipeline — fetch, enrich, strip, embed job listings.

Orchestrates four phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. StripPhase: LLM-based boilerplate removal (Azure OpenAI)
4. EmbedPhase: per-model embedding generation

Phases communicate via an EventQueue (queue.SimpleQueue[SyncEvent]). The CLI
drains the queue on a consumer thread for real-time progress display. Callers
that don't care about events can pass events=None — a local queue is created
and events are silently discarded.
"""

import queue
from pathlib import Path

from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.store import JobStore
from jobbuddy.sync.types import (
    CompanySkipped,
    Done,
    EventQueue,
    SyncResult,
)
from jobbuddy.sync.embed import EmbedPhase
from jobbuddy.sync.enrich import EnrichPhase
from jobbuddy.sync.fetch import FetchPhase
from jobbuddy.sync.strip import StripPhase


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def sync_jobs(
    company_slug: str | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    events: EventQueue | None = None,
    db_path: Path | str | None = None,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the SQLite cache.

    Four phases: fetch → enrich → strip → embed. Each phase is independent
    and communicates through the store. Progress events are pushed onto the
    EventQueue for real-time display.
    """
    import random

    if events is None:
        events = queue.SimpleQueue()

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
                else:
                    events.put(CompanySkipped(c.slug, "recently synced"))
            targets = filtered

        if not targets:
            events.put(Done())
            return []

        # Shuffle to spread same-platform companies across time
        random.shuffle(targets)

        # Phase 1: Fetch
        results, slugs_to_embed = FetchPhase(store, targets, max_workers, events).run()

        # Phase 2: Enrich descriptions for stub fetchers
        if slugs_to_embed:
            EnrichPhase(store, slugs_to_embed, targets, max_workers, events).run()

        # Phase 3: Strip boilerplate (global — backfills existing jobs too)
        # StripPhase(store, events).run()

        # Phase 4: Embed
        if slugs_to_embed:
            EmbedPhase(store, slugs_to_embed, events).run()

        events.put(Done())
        return results
    finally:
        store.close()
