"""Sync pipeline -- fetch, enrich, strip, embed job listings.

Orchestrates four phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. StripPhase: LLM-based boilerplate removal (requires OpenAI API)
4. EmbedPhase: embedding generation (requires OpenAI API)

All four phases run by default. Strip and embed require OpenAI credentials
(JOBBUDDY_OPENAI_API_KEY) — sync fails fast if they're missing.

Phases use DB-as-queue: each polls PostgreSQL for work items and updates
PhaseState objects for live display. No event queue for inter-phase
coordination -- phases are independent and idempotent.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from jobbuddy.models import Company
from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import Settings, get_settings
from jobbuddy.store import JobStore
from jobbuddy.sync.display import SyncDisplayState
from jobbuddy.sync.types import (
    CompanySkipped,
    Done,
    EventQueue,
    SyncResult,
)

__all__ = ["sync_jobs", "validate_sync_config", "SyncConfig", "SyncResult", "VALID_PHASES"]
from jobbuddy.sync.embed import EmbedPhase
from jobbuddy.sync.enrich import EnrichPhase
from jobbuddy.sync.fetch import FetchPhase
from jobbuddy.sync.strip import StripPhase


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


VALID_PHASES = {"fetch", "enrich", "strip", "embed"}
_OPENAI_PHASES = {"strip", "embed"}


@dataclass
class SyncConfig:
    """Validated sync configuration. Built by validate_sync_config()."""

    phases: set[str]
    conninfo: str
    targets: list[Company] = field(default_factory=list)
    company_slugs: list[str] | None = None
    stale_hours: float | None = None
    max_workers: int = 5
    force_strip: bool = False


def validate_sync_config(
    *,
    phases: set[str] | None = None,
    company_slugs: list[str] | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    force_strip: bool = False,
    settings: Settings | None = None,
) -> SyncConfig:
    """Validate all sync preconditions up front. Returns SyncConfig or raises ValueError.

    Checks: phase names valid, OpenAI key present for strip/embed, company slugs
    resolve to known companies with ATS config.
    """
    if settings is None:
        settings = get_settings()

    # Resolve phases
    resolved_phases = phases if phases is not None else VALID_PHASES
    invalid = resolved_phases - VALID_PHASES
    if invalid:
        raise ValueError(
            f"Invalid phase(s): {', '.join(sorted(invalid))}. "
            f"Valid phases: {', '.join(sorted(VALID_PHASES))}"
        )

    # OpenAI required for strip/embed
    needs_openai = resolved_phases & _OPENAI_PHASES
    if needs_openai and not settings.has_openai:
        raise ValueError(
            f"JOBBUDDY_OPENAI_API_KEY required for {', '.join(sorted(needs_openai))} phase(s)"
        )

    # Resolve company targets
    targets: list[Company] = []
    if company_slugs:
        for cs in company_slugs:
            company = lookup_by_name(cs)
            if not company:
                raise ValueError(f"Unknown company: {cs}")
            if not company.ats:
                raise ValueError(f"No ATS configured for {company.name}")
            targets.append(company)

    return SyncConfig(
        phases=resolved_phases,
        conninfo=settings.pg_conninfo,
        targets=targets,
        company_slugs=company_slugs,
        stale_hours=stale_hours,
        max_workers=max_workers,
        force_strip=force_strip,
    )


def sync_jobs(
    company_slugs: list[str] | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    events: EventQueue | None = None,
    conninfo: str | None = None,
    display_state: SyncDisplayState | None = None,
    phases: set[str] | None = None,
    force_strip: bool = False,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the PostgreSQL cache.

    Fetch runs first, then enrich/strip/embed run concurrently. Each phase
    polls the DB for work; upstream_done events signal when it's safe to stop
    polling (enrich_done -> strip, strip_done -> embed). Strip starts processing
    full-fetcher jobs immediately while enrich is still fetching descriptions
    for stub fetchers.

    Args:
        company_slugs: Sync only these companies (None = all).
        stale_hours: Skip companies synced within this many hours.
        max_workers: Thread pool size for fetch phase.
        events: Legacy event queue (kept for backward compatibility).
        conninfo: PostgreSQL connection string (None = default from settings).
        display_state: Shared display state for Rich Live TUI.
        phases: Which phases to run (None = all). Must be subset of VALID_PHASES.
        force_strip: Clear existing stripped descriptions before strip phase.
    """
    import random

    # Resolve which phases to run
    if phases is not None:
        invalid = phases - VALID_PHASES
        if invalid:
            raise ValueError(
                f"Invalid phase(s): {', '.join(sorted(invalid))}. "
                f"Valid phases: {', '.join(sorted(VALID_PHASES))}"
            )
        run_phases = phases
    else:
        run_phases = VALID_PHASES

    if events is None:
        events = queue.SimpleQueue()

    if display_state is None:
        display_state = SyncDisplayState()

    registry = list_companies()

    # Resolve conninfo
    if conninfo is None:
        conninfo = get_settings().pg_conninfo

    # Fetch phase: build targets and run
    results: list[SyncResult] = []
    slugs_to_embed: list[str] = []

    if "fetch" in run_phases:
        # Build target list (validate before opening DB)
        if company_slugs:
            targets = []
            for cs in company_slugs:
                company = lookup_by_name(cs)
                if not company:
                    raise ValueError(f"Unknown company: {cs}")
                if not company.ats:
                    raise ValueError(f"No ATS configured for {company.name}")
                targets.append(company)
        else:
            targets = [c for c in registry.values() if c.ats is not None]

        store = JobStore(conninfo)
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
        finally:
            store.close()
    else:
        events.put(Done())
        # When skipping fetch, build targets for enrich from registry
        if company_slugs:
            targets = []
            for cs in company_slugs:
                company = lookup_by_name(cs)
                if company and company.ats:
                    targets.append(company)
        else:
            targets = [c for c in registry.values() if c.ats is not None]

        # Discover stub-fetcher slugs so enrich can fill missing descriptions
        if "enrich" in run_phases:
            from jobbuddy.fetchers import has_descriptions_in_listing
            slugs_to_embed = [
                c.slug for c in targets
                if c.ats and not has_descriptions_in_listing(c.ats)
            ]

    # Phases 2-4: Enrich, strip, embed run concurrently.
    # Each phase polls the DB for work. upstream_done events chain them
    # so downstream phases keep polling until upstream finishes producing.
    #   enrich_done -> strip keeps polling for newly-enriched descriptions
    #   strip_done  -> embed keeps polling for newly-stripped descriptions
    enrich_done = threading.Event()
    strip_done = threading.Event()

    settings = get_settings()

    # Strip and embed require OpenAI credentials — fail loud if missing.
    openai_phases = run_phases & {"strip", "embed"}
    if openai_phases and not settings.has_openai:
        raise ValueError(
            f"JOBBUDDY_OPENAI_API_KEY required for {', '.join(sorted(openai_phases))} phase(s)"
        )

    # Ensure schema/migrations run in the main thread before spawning
    # phase threads -- avoids race where multiple threads try to migrate
    # simultaneously.
    JobStore(conninfo).close()

    # Force-strip: clear existing stripped descriptions before strip phase
    if force_strip and "strip" in run_phases:
        store = JobStore(conninfo)
        store.clear_stripped_descriptions()
        store.close()

    # Build enrich phase (if selected and any stub fetchers succeeded)
    enrich_phase = None
    if "enrich" in run_phases and slugs_to_embed:
        enrich_phase = EnrichPhase(
            conninfo,
            slugs=slugs_to_embed,
            targets=targets,
            display=display_state.enrich,
            max_workers=max_workers,
        )

    # Build strip phase (if selected)
    strip_phase = None
    if "strip" in run_phases:
        strip_phase = StripPhase(
            conninfo,
            display=display_state.strip,
            slugs=company_slugs,
            upstream_done=enrich_done,
        )

    # Build embed phase (if selected)
    embed_phase = None
    if "embed" in run_phases:
        embed_phase = EmbedPhase(
            conninfo,
            display=display_state.embed,
            slugs=company_slugs,
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

    # If enrich is skipped, pre-set its done event so strip doesn't wait
    if "enrich" not in run_phases:
        enrich_done.set()

    # If strip is skipped, pre-set its done event so embed doesn't wait
    if "strip" not in run_phases:
        strip_done.set()

    threads: list[threading.Thread] = []
    threads.append(threading.Thread(target=run_enrich, daemon=True))
    threads.append(threading.Thread(target=run_strip, daemon=True))
    threads.append(threading.Thread(target=run_embed, daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Signal fetch consumer that all phases are complete.
    # (When fetch is skipped, Done was already sent above.)
    if "fetch" in run_phases:
        events.put(Done())
    return results
