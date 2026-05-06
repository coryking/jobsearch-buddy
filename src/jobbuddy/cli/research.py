"""`jsb research-companies` -- fill company bios via Azure Responses + web_search.

Plain-logging variant; mirrors `jsb sync`. No Rich Live. Errors are
visible, timestamped, and the heartbeat thread emits a phase status
line every N seconds so a long-running backfill is visibly alive in
journalctl rather than silently hanging.
"""

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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log per-item activity at DEBUG.",
    ),
    heartbeat: float = typer.Option(
        30.0, "--heartbeat",
        help="Seconds between phase status heartbeats. Set 0 to disable.",
    ),
):
    """Run before sync if you want company context in short_jds.

    Backfills any company missing a bio. Pass `-c/--company` (repeatable)
    to scope to specific companies. `--force` clears existing bios first
    — required to be paired with `-c` to avoid an unscoped global wipe.
    """
    from jobbuddy.cli.sync import _configure_logging
    from jobbuddy.registry import lookup_by_name
    from jobbuddy.settings import pg_conninfo_with_token
    from jobbuddy.store import JobStore
    from jobbuddy.sync import validate_sync_config
    from jobbuddy.sync.display import SyncDisplayState
    from jobbuddy.sync.heartbeat import HeartbeatLogger
    from jobbuddy.sync.research import ResearchPhase

    _configure_logging(verbose=verbose)

    if force and not company:
        log.error(
            "--force requires --company/-c. Refusing to wipe all bios."
            " Pass `--company X` (repeatable) to scope, or omit --force to"
            " skip already-filled rows."
        )
        raise SystemExit(1)

    try:
        validate_sync_config(phases={"research"})
    except ValueError as e:
        log.error("%s", e)
        raise SystemExit(1)

    conninfo = pg_conninfo_with_token()
    target_slugs: list[str] | None = None

    if company:
        resolved_slugs: list[str] = []
        for c in company:
            resolved = lookup_by_name(c)
            if not resolved:
                log.error("Unknown company: %s", c)
                raise SystemExit(1)
            resolved_slugs.append(resolved.slug)
        target_slugs = resolved_slugs

        store = JobStore(conninfo)
        try:
            if force:
                store.clear_company_bios(slugs=target_slugs)
                log.info("Cleared bios for: %s", ", ".join(sorted(target_slugs)))
            else:
                already_filled = {
                    r["slug"]
                    for r in store.conn.execute(
                        "SELECT slug FROM companies"
                        " WHERE long_bio IS NOT NULL AND slug = ANY(%s)",
                        (target_slugs,),
                    ).fetchall()
                }
                if already_filled:
                    log.info(
                        "Already have bios: %s. Use --force to re-research.",
                        ", ".join(sorted(already_filled)),
                    )
                    target_slugs = [s for s in target_slugs if s not in already_filled]
                    if not target_slugs:
                        return
        finally:
            store.close()

    state = SyncDisplayState()
    phase = ResearchPhase(conninfo, display=state.research, slugs=target_slugs)

    hb: HeartbeatLogger | None = None
    if heartbeat > 0:
        hb = HeartbeatLogger(state, interval_seconds=heartbeat)
        hb.start()

    try:
        phase.run()
    except KeyboardInterrupt:
        log.warning("research-companies interrupted by user")
        return
    finally:
        if hb is not None:
            hb.stop()

    rs = state.research
    log.info("research-companies complete: %d researched, %d errors", rs.done, rs.errors)
