"""Research command -- fill company bios via Azure Responses + web_search."""

from __future__ import annotations

import logging
from typing import Optional

import typer

from jobbuddy.cli import app, console

log = logging.getLogger(__name__)


@app.command(name="research-companies")
def research_companies(
    company: Optional[list[str]] = typer.Option(
        None, "--company", "-c",
        help="Research specific companies (repeatable). Omit for backfill: all unfilled.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-research even if a bio is already present",
    ),
):
    """Fill long_bio + short_bio for companies via Azure Responses API.

    Default mode is backfill: research every company where long_bio IS NULL.
    Pass --company/-c (repeatable) to scope to specific companies. --force
    clears existing bios first."""
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
    target_slugs: list[str] | None = None

    if company:
        resolved_slugs: list[str] = []
        for c in company:
            resolved = lookup_by_name(c)
            if not resolved:
                console.print(f"[red]Unknown company: {c}[/red]")
                raise SystemExit(1)
            resolved_slugs.append(resolved.slug)
        target_slugs = resolved_slugs

        store = JobStore(conninfo)
        try:
            if force:
                store.conn.execute(
                    "UPDATE companies SET long_bio = NULL, short_bio = NULL,"
                    " bio_researched_at = NULL, bio_model = NULL"
                    " WHERE slug = ANY(%s)",
                    (target_slugs,),
                )
            else:
                already = store.conn.execute(
                    "SELECT slug FROM companies"
                    " WHERE long_bio IS NOT NULL AND slug = ANY(%s)",
                    (target_slugs,),
                ).fetchall()
                if already:
                    filled = ", ".join(r["slug"] for r in already)
                    console.print(
                        f"[yellow]Already have bios: {filled}."
                        f" Use --force to re-research.[/yellow]"
                    )
                    target_slugs = [
                        s for s in target_slugs
                        if s not in {r["slug"] for r in already}
                    ]
                    if not target_slugs:
                        return
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
    phase = ResearchPhase(conninfo, display=state.research, slugs=target_slugs)

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
