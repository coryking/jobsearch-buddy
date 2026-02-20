"""Sync pipeline -- fetch, enrich, strip, embed job listings.

Orchestrates four phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. StripPhase: LLM-based boilerplate removal (Azure OpenAI gpt-5-nano)
4. EmbedPhase: embedding generation (Azure OpenAI text-embedding-3-small)

Phases use DB-as-queue: each polls SQLite for work items and updates
PhaseState objects for live display. No event queue for inter-phase
coordination -- phases are independent and idempotent.
"""

import queue
from pathlib import Path

from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState, SyncDisplayState
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
    display_state: SyncDisplayState | None = None,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the SQLite cache.

    Four phases: fetch -> enrich -> strip -> embed. Each phase is independent
    and communicates through the store. Progress is reflected in the
    SyncDisplayState for live TUI rendering.

    Args:
        company_slug: Sync only this company (None = all).
        stale_hours: Skip companies synced within this many hours.
        max_workers: Thread pool size for fetch phase.
        events: Legacy event queue (kept for backward compatibility).
        db_path: Path to SQLite DB (None = default from settings).
        display_state: Shared display state for Rich Live TUI.
    """
    import random

    if events is None:
        events = queue.SimpleQueue()

    if display_state is None:
        display_state = SyncDisplayState()

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

    # Resolve DB path
    if db_path is None:
        db_path = get_settings().db_path
    db_path_str = str(db_path)

    store = JobStore(db_path)

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
            EnrichPhase(
                db_path_str,
                slugs=slugs_to_embed,
                targets=targets,
                display=display_state.enrich,
                max_workers=max_workers,
            ).run()

        # Phase 3: Strip boilerplate (global -- backfills existing jobs too)
        settings = get_settings()
        if settings.azure_openai_api_key and settings.azure_openai_endpoint:
            StripPhase(
                db_path_str,
                display=display_state.strip,
            ).run()

        # Phase 4: Embed
        EmbedPhase(
            db_path_str,
            display=display_state.embed,
        ).run()

        events.put(Done())
        return results
    finally:
        store.close()
