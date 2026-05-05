"""Sync pipeline command with phase selection."""

import logging
from typing import Optional

import typer

from jobbuddy.cli import app, console

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def sync(
    phases: Optional[list[str]] = typer.Argument(None, help="Phases to run: fetch, enrich, strip, embed, research (default: all)"),
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
    from jobbuddy.sync import sync_jobs, validate_sync_config
    from jobbuddy.sync.display import SyncDisplayState, create_live, print_sync_summary

    # Validate all preconditions up front — before any I/O
    phase_set = set(phases) if phases else None
    try:
        config = validate_sync_config(
            phases=phase_set,
            company_slugs=company or None,
            stale_hours=stale,
            force_strip=force,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    interactive = console.is_terminal
    if not interactive:
        _setup_logging()

    state = SyncDisplayState()

    if not interactive:
        phase_label = ", ".join(sorted(config.phases)) if phase_set else "all"
        log.info("jsb sync starting (phases: %s)", phase_label)

    def _run():
        return sync_jobs(
            company_slugs=company or None,
            stale_hours=stale,
            display_state=state,
            phases=phase_set,
            force_strip=force,
        )

    # Determine display filter for TUI
    _PHASE_ORDER = ["fetch", "enrich", "strip", "embed", "research"]
    filter_phases = (
        [p.capitalize() for p in sorted(config.phases, key=lambda p: _PHASE_ORDER.index(p))]
        if phase_set else None
    )

    try:
        if interactive:
            with create_live(console, state, filter_phases=filter_phases):
                results = _run()
        else:
            results = _run()
    except KeyboardInterrupt:
        return
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    _print_summary(state, results, "fetch" in config.phases, interactive)


def _setup_logging() -> None:
    """Configure logging for non-interactive (systemd/cron) runs.

    Root logger stays at WARNING to suppress httpx/urllib3 noise;
    only jobbuddy loggers get INFO.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("jobbuddy").setLevel(logging.INFO)


def _print_summary(
    state,
    results: list,
    ran_fetch: bool,
    interactive: bool,
) -> None:
    """Print sync results — Rich table for interactive, log lines otherwise."""
    from jobbuddy.sync.display import print_sync_summary

    if not results and ran_fetch:
        if interactive:
            console.print("[dim]Nothing to sync.[/dim]")
        else:
            log.info("Nothing to sync")
        return

    if interactive:
        print_sync_summary(console, state)
    else:
        for phase in state.phases:
            if phase.status != "pending":
                log.info("%s: %d done, %d errors", phase.name, phase.done, phase.errors)
        for r in results:
            if not r.ok:
                log.error("%s: %s", r.slug, r.error)
