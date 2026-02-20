"""Sync pipeline commands: sync, strip, embed."""

import queue
import threading
from typing import Optional

import humanize
import typer

from jobbuddy.cli import app, console
from jobbuddy.registry import list_companies, lookup_by_name
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
                fetch.detail = slug

            case FetchProgress(slug=slug, fetched=fetched, total=total):
                fetch.detail = f"{slug}: {humanize.intcomma(fetched)}/{humanize.intcomma(total)} jobs"

            case FetchResult(result=sr):
                if sr.ok:
                    fetch.advance(detail=sr.slug)
                    if sr.job_count:
                        fetch.add_to_info_counter(sr.job_count, " jobs")
                else:
                    fetch.record_error()
                    fetch.advance(detail=sr.slug)
                if fetch.total is not None and fetch.done >= fetch.total:
                    fetch.finish()

            case CompanySkipped(slug=slug, reason=reason):
                fetch.advance(detail=f"{slug} (skipped)")
                if fetch.total is not None and fetch.done >= fetch.total:
                    fetch.finish()

            case RetryEvent(slug=slug, job_id=job_id, attempt=attempt, max_attempts=max_attempts, wait_seconds=wait, reason=reason):
                target = slug if slug else job_id
                fetch.detail = f"{target}: retry {attempt}/{max_attempts}"


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
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Sync only this company"),
    stale: Optional[float] = typer.Option(None, "--stale", "-s", help="Skip companies synced within N hours"),
):
    """Sync job listings from ATS boards into the local cache."""
    from jobbuddy.sync import sync_jobs
    from jobbuddy.sync.display import SyncDisplayState, create_live, print_sync_summary

    registry = list_companies()
    scrapeable = sum(1 for c in registry.values() if c.ats is not None)
    target_count = 1 if company else scrapeable

    state = SyncDisplayState()
    # Pre-start the fetch phase display with the target count
    state.fetch.start(target_count)

    with create_live(console, state):
        try:
            results = _run_fetch_with_events(
                lambda events: sync_jobs(
                    company_slug=company,
                    stale_hours=stale,
                    events=events,
                    display_state=state,
                ),
                state,
            )
        except KeyboardInterrupt:
            return
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    # Summary after Live exits
    if not results:
        console.print("[dim]Nothing to sync.[/dim]")
        return

    print_sync_summary(console, state)


@app.command()
def strip(
    force: bool = typer.Option(False, "--force", "-f", help="Re-strip jobs that already have stripped descriptions"),
):
    """Strip boilerplate from cached job descriptions using Azure OpenAI."""
    from jobbuddy.store import JobStore
    from jobbuddy.sync.display import SyncDisplayState, create_live, print_sync_summary
    from jobbuddy.sync.strip import StripPhase

    settings = get_settings()
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        console.print("[red]Azure OpenAI not configured.[/red]")
        console.print("[dim]Set JOBBUDDY_AZURE_OPENAI_API_KEY and JOBBUDDY_AZURE_OPENAI_ENDPOINT[/dim]")
        raise SystemExit(1)

    if not settings.db_path.exists():
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
        raise SystemExit(0)

    if force:
        store = JobStore()
        cleared = store.clear_stripped_descriptions()
        store.close()
        if cleared:
            console.print(f"[dim]Cleared {cleared} existing stripped descriptions[/dim]")

    state = SyncDisplayState()

    with create_live(console, state, filter_phases=["Strip"]):
        phase = StripPhase(str(settings.db_path), display=state.strip)
        try:
            phase.run()
        except KeyboardInterrupt:
            phase.shutdown()

    print_sync_summary(console, state)


@app.command()
def embed(
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Only embed jobs for this company"),
):
    """Generate embeddings for cached jobs (without fetching or enriching)."""
    from jobbuddy.store import JobStore
    from jobbuddy.sync.display import SyncDisplayState, create_live, print_sync_summary
    from jobbuddy.sync.embed import EmbedPhase

    if not get_settings().db_path.exists():
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
        raise SystemExit(0)

    settings = get_settings()
    state = SyncDisplayState()

    with create_live(console, state, filter_phases=["Embed"]):
        phase = EmbedPhase(str(settings.db_path), display=state.embed)
        try:
            phase.run()
        except KeyboardInterrupt:
            phase.shutdown()

    print_sync_summary(console, state)
