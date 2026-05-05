"""Sync orchestration -- validates config and runs the phase pipeline.

Phases wired today:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. ResearchPhase: company bio research (requires Azure Responses API)

DistillPhase is pending Unit 2 of the Phase 1 redesign plan. It will
require OpenAI credentials; add it to VALID_PHASES and _OPENAI_PHASES at
the same time as wiring the phase, otherwise validate_sync_config will
silently skip the credential check.

Phases use DB-as-queue: each polls PostgreSQL for work items and updates
PhaseState objects for live display. Research is independent of the job
pipeline -- it polls companies, not jobs."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

from jobbuddy.fetchers import has_descriptions_in_listing
from jobbuddy.models import Company
from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import Settings, get_settings, pg_conninfo_with_token
from jobbuddy.store import JobStore
from jobbuddy.sync.display import SyncDisplayState
from jobbuddy.sync.enrich import EnrichPhase
from jobbuddy.sync.fetch import FetchPhase
from jobbuddy.sync.research import ResearchPhase
from jobbuddy.sync.types import SyncResult


VALID_PHASES = {"fetch", "enrich", "research"}
# Phases that require OpenAI credentials. Empty until Unit 2 wires in
# "distill" -- add "distill" here at the same time as adding it to
# VALID_PHASES, otherwise validate_sync_config silently skips the check.
_OPENAI_PHASES: set[str] = set()
_RESEARCH_PHASES = {"research"}


@dataclass
class SyncConfig:
    """Validated sync configuration. Built by validate_sync_config()."""

    phases: set[str]
    conninfo: str
    targets: list[Company] = field(default_factory=list)
    company_slugs: list[str] | None = None
    stale_hours: float | None = None
    max_workers: int = 5


def validate_sync_config(
    *,
    phases: set[str] | None = None,
    company_slugs: list[str] | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    settings: Settings | None = None,
) -> SyncConfig:
    """Validate all sync preconditions up front. Returns SyncConfig or raises ValueError."""
    if settings is None:
        settings = get_settings()

    resolved_phases = phases if phases is not None else VALID_PHASES
    invalid = resolved_phases - VALID_PHASES
    if invalid:
        raise ValueError(
            f"Invalid phase(s): {', '.join(sorted(invalid))}. "
            f"Valid phases: {', '.join(sorted(VALID_PHASES))}"
        )

    needs_openai = resolved_phases & _OPENAI_PHASES
    if needs_openai and not settings.has_openai:
        raise ValueError(
            f"JOBBUDDY_OPENAI_API_KEY required for {', '.join(sorted(needs_openai))} phase(s)"
        )

    needs_research = resolved_phases & _RESEARCH_PHASES
    if needs_research and not settings.has_research:
        raise ValueError(
            "Research phase requires an Azure OpenAI endpoint. "
            "Set JOBBUDDY_RESEARCH_ENDPOINT or JOBBUDDY_OPENAI_BASE_URL."
        )

    targets: list[Company] = []
    if company_slugs:
        targets = _resolve_company_targets(company_slugs)

    return SyncConfig(
        phases=resolved_phases,
        conninfo=pg_conninfo_with_token(settings),
        targets=targets,
        company_slugs=company_slugs,
        stale_hours=stale_hours,
        max_workers=max_workers,
    )


def _resolve_company_targets(company_slugs: list[str]) -> list[Company]:
    """Resolve company slug/name strings to Company objects."""
    targets = []
    for cs in company_slugs:
        company = lookup_by_name(cs)
        if not company:
            raise ValueError(f"Unknown company: {cs}")
        if not company.ats:
            raise ValueError(f"No ATS configured for {company.name}")
        targets.append(company)
    return targets


def sync_jobs(
    company_slugs: list[str] | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    conninfo: str | None = None,
    display_state: SyncDisplayState | None = None,
    phases: set[str] | None = None,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the PostgreSQL cache.

    Fetch runs first; enrich and research run concurrently after.
    Research is independent of the job pipeline -- it polls companies.

    Callers should use validate_sync_config() first to check preconditions.
    """
    run_phases = phases if phases is not None else VALID_PHASES

    if display_state is None:
        display_state = SyncDisplayState()

    registry = list_companies()

    if conninfo is None:
        conninfo = pg_conninfo_with_token()

    resolved_slugs: list[str] | None = None
    if company_slugs:
        resolved_slugs = [c.slug for c in _resolve_company_targets(company_slugs)]

    results: list[SyncResult] = []
    slugs_to_enrich: list[str] = []

    if "fetch" in run_phases:
        if company_slugs:
            targets = _resolve_company_targets(company_slugs)
        else:
            targets = [c for c in registry.values() if c.ats is not None]

        store = JobStore(conninfo)
        try:
            if stale_hours is not None:
                targets = [c for c in targets if store.is_stale(c.slug, stale_hours)]

            if not targets:
                return []

            random.shuffle(targets)

            results, slugs_to_enrich = FetchPhase(
                store, targets, max_workers, display=display_state.fetch,
            ).run()
        finally:
            store.close()
    else:
        if company_slugs:
            targets = _resolve_company_targets(company_slugs)
        else:
            targets = [c for c in registry.values() if c.ats is not None]

        if "enrich" in run_phases:
            slugs_to_enrich = [
                c.slug for c in targets
                if c.ats and not has_descriptions_in_listing(c.ats)
            ]

    # Ensure schema/migrations run in main thread before spawning phase threads.
    JobStore(conninfo).close()

    enrich_phase = None
    if "enrich" in run_phases and slugs_to_enrich:
        enrich_phase = EnrichPhase(
            conninfo,
            slugs=slugs_to_enrich,
            targets=targets,
            display=display_state.enrich,
            max_workers=max_workers,
        )

    research_phase = None
    if "research" in run_phases:
        research_phase = ResearchPhase(
            conninfo,
            display=display_state.research,
            slugs=resolved_slugs,
        )

    def run_enrich() -> None:
        if enrich_phase:
            enrich_phase.run()

    def run_research() -> None:
        if research_phase:
            research_phase.run()

    threads = [
        threading.Thread(target=run_enrich, daemon=True),
        threading.Thread(target=run_research, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results
