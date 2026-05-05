"""Sync orchestration -- validates config and runs the phase pipeline.

Five phases:
1. FetchPhase: parallel company fetching via ThreadPoolExecutor
2. EnrichPhase: description enrichment for stub fetchers
3. StripPhase: LLM-based boilerplate removal (requires OpenAI API)
4. EmbedPhase: embedding generation (requires OpenAI API)
5. ResearchPhase: company bio research (requires Azure Responses API)

All phases run by default. Strip/embed need OpenAI credentials; research
needs an Azure Responses-API endpoint. Sync fails fast at startup if a
selected phase's credentials are missing.

Phases use DB-as-queue: each polls PostgreSQL for work items and updates
PhaseState objects for live display. upstream_done events chain enrich →
strip → embed so downstream phases keep polling until upstream is done.
Research is independent of the job pipeline -- it polls companies, not
jobs."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

from jobbuddy.models import Company
from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import Settings, get_settings, pg_conninfo_with_token
from jobbuddy.store import JobStore
from jobbuddy.sync.display import SyncDisplayState
from jobbuddy.sync.embed import EmbedPhase
from jobbuddy.sync.enrich import EnrichPhase
from jobbuddy.sync.fetch import FetchPhase
from jobbuddy.sync.research import ResearchPhase
from jobbuddy.sync.strip import StripPhase
from jobbuddy.sync.types import SyncResult


VALID_PHASES = {"fetch", "enrich", "strip", "embed", "research"}
_OPENAI_PHASES = {"strip", "embed"}
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

    Checks: phase names valid, OpenAI key present for strip/embed, Azure
    research endpoint present for research, company slugs resolve to known
    companies with ATS config.
    """
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
        for cs in company_slugs:
            company = lookup_by_name(cs)
            if not company:
                raise ValueError(f"Unknown company: {cs}")
            if not company.ats:
                raise ValueError(f"No ATS configured for {company.name}")
            targets.append(company)

    return SyncConfig(
        phases=resolved_phases,
        conninfo=pg_conninfo_with_token(settings),
        targets=targets,
        company_slugs=company_slugs,
        stale_hours=stale_hours,
        max_workers=max_workers,
        force_strip=force_strip,
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
    force_strip: bool = False,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the PostgreSQL cache.

    Fetch runs first, then enrich/strip/embed/research run concurrently.
    Each phase polls the DB for work; upstream_done events signal when it's
    safe to stop polling (enrich_done -> strip, strip_done -> embed). Strip
    starts processing full-fetcher jobs immediately while enrich is still
    fetching descriptions for stub fetchers. Research is independent —
    polls the companies table.

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
    slugs_to_embed: list[str] = []

    if "fetch" in run_phases:
        if company_slugs:
            targets = _resolve_company_targets(company_slugs)
        else:
            targets = [c for c in registry.values() if c.ats is not None]

        store = JobStore(conninfo)
        try:
            if stale_hours is not None:
                filtered = []
                for c in targets:
                    if store.is_stale(c.slug, stale_hours):
                        filtered.append(c)
                targets = filtered

            if not targets:
                return []

            # Shuffle to spread same-platform companies across time
            random.shuffle(targets)

            results, slugs_to_embed = FetchPhase(
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
            from jobbuddy.fetchers import has_descriptions_in_listing
            slugs_to_embed = [
                c.slug for c in targets
                if c.ats and not has_descriptions_in_listing(c.ats)
            ]

    # Concurrent phases coordinate via Events. Enrich -> strip -> embed
    # chain by upstream_done so downstream keeps polling until upstream is
    # done. Research is independent.
    enrich_done = threading.Event()
    strip_done = threading.Event()

    # Ensure schema is in place on the main thread before phase threads start.
    JobStore(conninfo).close()

    if force_strip and "strip" in run_phases:
        store = JobStore(conninfo)
        store.clear_stripped_descriptions()
        store.close()

    enrich_phase = None
    if "enrich" in run_phases and slugs_to_embed:
        enrich_phase = EnrichPhase(
            conninfo,
            slugs=slugs_to_embed,
            targets=targets,
            display=display_state.enrich,
            max_workers=max_workers,
        )

    strip_phase = None
    if "strip" in run_phases:
        strip_phase = StripPhase(
            conninfo,
            display=display_state.strip,
            slugs=resolved_slugs,
            upstream_done=enrich_done,
        )

    embed_phase = None
    if "embed" in run_phases:
        embed_phase = EmbedPhase(
            conninfo,
            display=display_state.embed,
            slugs=resolved_slugs,
            upstream_done=strip_done,
        )

    research_phase = None
    if "research" in run_phases:
        research_phase = ResearchPhase(
            conninfo,
            display=display_state.research,
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

    def run_research() -> None:
        if research_phase:
            research_phase.run()

    if "enrich" not in run_phases:
        enrich_done.set()
    if "strip" not in run_phases:
        strip_done.set()

    threads = [
        threading.Thread(target=run_enrich, daemon=True),
        threading.Thread(target=run_strip, daemon=True),
        threading.Thread(target=run_embed, daemon=True),
        threading.Thread(target=run_research, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results
