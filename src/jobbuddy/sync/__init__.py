"""Sync pipeline -- fetch, enrich, strip, embed job listings.

Orchestrates four phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. StripPhase: LLM-based boilerplate removal (requires OpenAI API)
4. EmbedPhase: embedding generation (requires OpenAI API)

Strip and embed are optional — they only run when OpenAI credentials are
configured (JOBBUDDY_OPENAI_API_KEY). Without credentials, sync runs
fetch + enrich only.

Phases use DB-as-queue: each polls SQLite for work items and updates
PhaseState objects for live display. No event queue for inter-phase
coordination -- phases are independent and idempotent.
"""

import queue
import threading
from pathlib import Path

from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore
from jobbuddy.sync.display import SyncDisplayState
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

    Fetch runs first, then enrich/strip/embed run concurrently. Each phase
    polls the DB for work; upstream_done events signal when it's safe to stop
    polling (enrich_done → strip, strip_done → embed). Strip starts processing
    full-fetcher jobs immediately while enrich is still fetching descriptions
    for stub fetchers.

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

        # Phases 2-4: Enrich, strip, embed run concurrently.
        # Each phase polls the DB for work. upstream_done events chain them
        # so downstream phases keep polling until upstream finishes producing.
        #   enrich_done → strip keeps polling for newly-enriched descriptions
        #   strip_done  → embed keeps polling for newly-stripped descriptions
        enrich_done = threading.Event()
        strip_done = threading.Event()

        settings = get_settings()
        has_openai = settings.has_openai

        # Build enrich phase (if any stub fetchers succeeded)
        enrich_phase = None
        if slugs_to_embed:
            enrich_phase = EnrichPhase(
                db_path_str,
                slugs=slugs_to_embed,
                targets=targets,
                display=display_state.enrich,
                max_workers=max_workers,
            )

        # Build strip phase (if OpenAI credentials configured)
        strip_phase = None
        if has_openai:
            strip_phase = StripPhase(
                db_path_str,
                display=display_state.strip,
                slug=company_slug,
                upstream_done=enrich_done,
            )

        # Build embed phase (if OpenAI credentials configured)
        embed_phase = None
        if has_openai:
            embed_phase = EmbedPhase(
                db_path_str,
                display=display_state.embed,
                slug=company_slug,
                upstream_done=strip_done,
            )

        def run_enrich() -> None:
            try:
                if enrich_phase:
                    enrich_phase.run()
            finally:
                enrich_done.set()

        def run_strip() -> None:
            try:
                if strip_phase:
                    strip_phase.run()
            finally:
                strip_done.set()

        def run_embed() -> None:
            if embed_phase:
                embed_phase.run()

        threads: list[threading.Thread] = []
        threads.append(threading.Thread(target=run_enrich, daemon=True))
        threads.append(threading.Thread(target=run_strip, daemon=True))
        threads.append(threading.Thread(target=run_embed, daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events.put(Done())
        return results
    finally:
        store.close()
