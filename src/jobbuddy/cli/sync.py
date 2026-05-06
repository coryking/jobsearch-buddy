"""`jsb sync` -- run the phase pipeline with plain timestamped logging.

Output is stdlib `logging` to stderr with `asctime` so:

- systemd / upstart journals get useful per-line timestamps;
- LLM operators reading a captured log file can grep for `phase=`,
  `WriteQueue`, `ERROR`, etc.;
- failures show full tracebacks instead of a swallowed Rich Live frame.

There is no interactive Rich Live mode anymore. The single failure mode
that motivated the rewrite -- a WriteQueue drop hidden behind the live
table that ate $60 of distill calls -- can no longer happen: WriteQueue
errors are now fatal and crash the process with a traceback.
"""

import logging
from typing import Optional

import typer

from jobbuddy.cli import app, console

log = logging.getLogger(__name__)


@app.command()
def sync(
    phases: Optional[list[str]] = typer.Argument(
        None,
        help="Phases to run: fetch, enrich, research, distill (default: all)",
    ),
    company: Optional[list[str]] = typer.Option(
        None, "--company", "-c", help="Sync specific companies (repeatable)",
    ),
    stale: Optional[float] = typer.Option(
        None, "--stale", "-s", help="Skip companies synced within N hours",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Log per-item activity at DEBUG. Default INFO emits one heartbeat per phase every 30s.",
    ),
    heartbeat: float = typer.Option(
        30.0, "--heartbeat",
        help="Seconds between phase status heartbeats. Set 0 to disable.",
    ),
):
    """Sync job listings from ATS boards into PostgreSQL.

    Run specific phases by passing them as arguments:

        jsb sync                    # all phases (default)
        jsb sync fetch              # fetch only
        jsb sync distill -v         # distill with per-job DEBUG logs
    """
    from jobbuddy.sync import VALID_PHASES, sync_jobs, validate_sync_config
    from jobbuddy.sync.display import SyncDisplayState
    from jobbuddy.sync.heartbeat import HeartbeatLogger

    _configure_logging(verbose=verbose)

    phase_set = set(phases) if phases else None
    try:
        config = validate_sync_config(
            phases=phase_set,
            company_slugs=company or None,
            stale_hours=stale,
        )
    except ValueError as e:
        log.error("%s", e)
        raise SystemExit(1)

    phase_label = ", ".join(sorted(config.phases)) if phase_set else "all"
    log.info("jsb sync starting (phases: %s)", phase_label)

    state = SyncDisplayState()

    hb: HeartbeatLogger | None = None
    if heartbeat > 0:
        hb = HeartbeatLogger(state, interval_seconds=heartbeat)
        hb.start()

    try:
        results = sync_jobs(
            company_slugs=company or None,
            stale_hours=stale,
            display_state=state,
            phases=phase_set,
        )
    except KeyboardInterrupt:
        log.warning("jsb sync interrupted by user")
        return
    except ValueError as e:
        log.error("%s", e)
        raise SystemExit(1)
    finally:
        if hb is not None:
            hb.stop()

    _log_summary(state, results, ran_fetch="fetch" in config.phases)


def _configure_logging(*, verbose: bool) -> None:
    """Root at WARNING, jobbuddy at INFO (DEBUG with -v).

    Default behavior: only `jobbuddy.*` loggers emit INFO; everything
    else (azure.*, httpx, openai, psycopg, ...) is silenced unless they
    log at WARNING+. With -v, jobbuddy drops to DEBUG and third-party
    libs are unmuzzled to INFO so HTTP/SDK chatter is visible during
    investigation.
    """
    from jobbuddy.logctx import install as install_logctx
    install_logctx()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(ctx)s%(name)s: %(message)s%(suffix)s",
    )
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("jobbuddy").setLevel(logging.DEBUG)
    else:
        logging.getLogger("jobbuddy").setLevel(logging.INFO)


def _log_summary(state, results, *, ran_fetch: bool) -> None:
    """Final summary as plain log lines (no Rich)."""
    if not results and ran_fetch:
        log.info("Nothing to sync")
        return

    total_done = 0
    total_errors = 0
    for phase in state.phases:
        if phase.status == "pending":
            continue
        total_done += phase.done
        total_errors += phase.errors
        log.info(
            "summary phase=%s done=%d errors=%d",
            phase.name, phase.done, phase.errors,
        )

    for r in results:
        if not r.ok:
            log.error("fetch failed slug=%s error=%s", r.slug, r.error)

    if total_errors:
        log.warning("sync complete with %d errors across %d items", total_errors, total_done)
    else:
        log.info("sync complete: %d items, no errors", total_done)
