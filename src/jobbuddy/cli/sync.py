"""Sync pipeline command with phase selection."""

import queue
import threading
from typing import Optional

import humanize
import typer

from jobbuddy.cli import app, console
from jobbuddy.registry import list_companies
from jobbuddy.settings import get_settings


# ---------------------------------------------------------------------------
# Event consumer for fetch phase (FetchPhase still uses the event queue)
# ---------------------------------------------------------------------------


def _consume_events(events, display_state):
    """Drain the event queue on a daemon thread. Handles fetch-phase events only.

    The fetch phase still uses the legacy event queue pattern. Other phases
    (enrich, strip, embed) update PhaseState objects directly.
    """
    from jobbuddy.sync.types import (
        CompanySkipped,
        Done,
        FetchProgress,
        FetchResult,
        FetchStarted,
        RetryEvent,
    )

    fetch = display_state.fetch

    while True:
        event = events.get()

        match event:
            case Done():
                fetch.finish()
                break

            case FetchStarted(slug=slug):
                fetch.set_active_detail(slug, slug)

            case FetchProgress(slug=slug, fetched=fetched, total=total):
                fetch.set_active_detail(slug, f"{slug}: {humanize.intcomma(fetched)}/{humanize.intcomma(total)} jobs")

            case FetchResult(result=sr):
                fetch.remove_active_detail(sr.slug)
                if sr.ok:
                    fetch.advance()
                    if sr.job_count:
                        fetch.add_to_info_counter(sr.job_count, " jobs")
                else:
                    fetch.record_error()
                    fetch.advance()
                if fetch.total is not None and fetch.done >= fetch.total:
                    fetch.finish()

            case CompanySkipped(slug=slug, reason=reason):
                fetch.advance()
                if fetch.total is not None and fetch.done >= fetch.total:
                    fetch.finish()

            case RetryEvent(slug=slug, job_id=job_id, attempt=attempt, max_attempts=max_attempts, wait_seconds=wait, reason=reason):
                target = slug if slug else job_id
                fetch.set_active_detail(target, f"{target}: retry {attempt}/{max_attempts}")


def _run_fetch_with_events(fn, display_state):
    """Create queue, start consumer thread, call fn(events=queue), join."""
    from jobbuddy.sync.types import Done

    eq = queue.SimpleQueue()
    consumer = threading.Thread(
        target=_consume_events,
        args=(eq, display_state),
        daemon=True,
    )
    consumer.start()
    try:
        result = fn(events=eq)
    except BaseException:
        eq.put(Done())
        consumer.join(timeout=2)
        raise
    consumer.join(timeout=5)
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def sync(
    phases: Optional[list[str]] = typer.Argument(None, help="Phases to run: fetch, enrich, strip, embed (default: all)"),
    company: Optional[list[str]] = typer.Option(None, "--company", "-c", help="Sync specific companies (repeatable)"),
    stale: Optional[float] = typer.Option(None, "--stale", "-s", help="Skip companies synced within N hours"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-strip already-stripped jobs (strip phase only)"),
):
    """Sync job listings from ATS boards into the local cache.

    Run specific phases by passing them as arguments:

        jsb sync                    # all phases (default)
        jsb sync strip embed        # just strip + embed
        jsb sync fetch              # fetch only
    """
    from jobbuddy.sync import VALID_PHASES, sync_jobs
    from jobbuddy.sync.display import SyncDisplayState, create_live, print_sync_summary

    # Validate and resolve phase set
    if phases:
        phase_set = set(phases)
        invalid = phase_set - VALID_PHASES
        if invalid:
            console.print(
                f"[red]Invalid phase(s): {', '.join(sorted(invalid))}[/red]\n"
                f"[dim]Valid phases: {', '.join(sorted(VALID_PHASES))}[/dim]"
            )
            raise SystemExit(1)
    else:
        phase_set = None  # all phases

    # Phases requiring OpenAI
    openai_phases = {"strip", "embed"}
    selected = phase_set or VALID_PHASES
    if selected & openai_phases:
        settings = get_settings()
        if not settings.has_openai:
            console.print("[red]OpenAI API not configured.[/red]")
            console.print("[dim]Set JOBBUDDY_OPENAI_API_KEY (and optionally JOBBUDDY_OPENAI_BASE_URL)[/dim]")
            raise SystemExit(1)

    run_fetch = "fetch" in selected

    # Determine display filter: show only selected phases (capitalized to match PhaseState names)
    if phase_set:
        filter_phases = [p.capitalize() for p in sorted(phase_set, key=lambda p: ["fetch", "enrich", "strip", "embed"].index(p))]
    else:
        filter_phases = None  # show all

    state = SyncDisplayState()

    if run_fetch:
        registry = list_companies()
        scrapeable = sum(1 for c in registry.values() if c.ats is not None)
        target_count = len(company) if company else scrapeable
        state.fetch.start(target_count)

    with create_live(console, state, filter_phases=filter_phases):
        try:
            results = _run_fetch_with_events(
                lambda events: sync_jobs(
                    company_slugs=company or None,
                    stale_hours=stale,
                    events=events,
                    display_state=state,
                    phases=phase_set,
                    force_strip=force,
                ),
                state,
            )
        except KeyboardInterrupt:
            return
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    # Summary after Live exits
    if not results and run_fetch:
        console.print("[dim]Nothing to sync.[/dim]")
        return

    print_sync_summary(console, state)
