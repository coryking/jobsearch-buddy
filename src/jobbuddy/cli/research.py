"""Research command -- fill company bios via Azure Responses + web_search."""

from __future__ import annotations

import logging
from typing import Optional

import typer

from jobbuddy.cli import app, console

log = logging.getLogger(__name__)


@app.command(name="research-companies")
def research_companies(
    company: Optional[str] = typer.Argument(
        None, help="Company slug or name (omit for backfill: research all unfilled)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-research even if a bio is already present"
    ),
):
    """Fill long_bio + short_bio for companies via Azure Responses API.

    Default mode is backfill: research every company where long_bio IS NULL.
    Pass a company slug/name to research just that one. --force re-runs even
    if a bio already exists (clears it first)."""
    from jobbuddy.registry import lookup_by_name
    from jobbuddy.settings import pg_conninfo_with_token
    from jobbuddy.store import JobStore
    from jobbuddy.sync import validate_sync_config
    from jobbuddy.sync.display import SyncDisplayState, create_live
    from jobbuddy.sync.research import ResearchPhase

    try:
        validate_sync_config(phases={"research"})
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    conninfo = pg_conninfo_with_token()

    if company:
        resolved = lookup_by_name(company)
        if not resolved:
            console.print(f"[red]Unknown company: {company}[/red]")
            raise SystemExit(1)
        store = JobStore(conninfo)
        try:
            row = store.conn.execute(
                "SELECT long_bio FROM companies WHERE slug = %s", (resolved.slug,),
            ).fetchone()
            if row and row["long_bio"] and not force:
                console.print(
                    f"[yellow]{resolved.slug} already has a bio. Use --force to re-research.[/yellow]"
                )
                return
            if force:
                store.conn.execute(
                    "UPDATE companies SET long_bio = NULL, short_bio = NULL,"
                    " bio_researched_at = NULL, bio_model = NULL"
                    " WHERE slug = %s",
                    (resolved.slug,),
                )
        finally:
            store.close()
    elif force:
        store = JobStore(conninfo)
        try:
            store.conn.execute(
                "UPDATE companies SET long_bio = NULL, short_bio = NULL,"
                " bio_researched_at = NULL, bio_model = NULL"
            )
        finally:
            store.close()

    state = SyncDisplayState()
    phase = ResearchPhase(conninfo, display=state.research)

    interactive = console.is_terminal
    if not interactive:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
        logging.getLogger("jobbuddy").setLevel(logging.INFO)

    try:
        if interactive:
            with create_live(console, state, filter_phases=["Research"]):
                phase.run()
        else:
            phase.run()
    except KeyboardInterrupt:
        return

    rs = state.research
    console.print(
        f"\n[green]Research complete.[/green] {rs.done} researched, {rs.errors} errors."
    )
