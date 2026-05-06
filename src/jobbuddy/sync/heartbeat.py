"""Periodic phase-state log emitter.

Started by `sync_jobs()` (and by standalone phase commands), the
HeartbeatLogger samples each PhaseState on a fixed interval and emits
one INFO line per active phase. The line is structured key=value so it
greps cleanly and is easy for an LLM to parse when investigating a run:

    2026-05-06T12:34:56 INFO jobbuddy.sync.heartbeat: phase=Distill \\
        status=active done=142 total=500 pct=28 rate=18/m tok=4.2k/m \\
        info=85.3k_tok cached=87% workers=3/5 errors=2 drops=0 idle=4s

The thread is a daemon and shuts down when `stop()` is called or when
the owning sync run exits. It does not own any state of its own --
phases keep updating PhaseState directly; the heartbeat just reads.
"""

from __future__ import annotations

import logging
import threading

import humanize

from jobbuddy.sync.display import PhaseState, SyncDisplayState

log = logging.getLogger(__name__)


def _format_phase_line(phase: PhaseState) -> str:
    """Render one phase's metrics as a key=value log line.

    Missing metrics are emitted as ``-`` so column-grepping stays stable.
    """
    parts: list[str] = [
        f"phase={phase.name}",
        f"status={phase.status}",
        f"done={phase.done}",
    ]

    if phase.total is not None:
        parts.append(f"total={phase.total}")
        if phase.total > 0:
            pct = phase.done / phase.total * 100
            parts.append(f"pct={pct:.0f}")

    rpm = phase.rate.rate_per_min()
    parts.append(f"rate={int(rpm)}/m" if rpm is not None else "rate=-")

    tpm = phase.token_rate.tokens_per_min()
    parts.append(f"tok_rate={humanize.metric(tpm)}/m" if tpm is not None else "tok_rate=-")

    if phase.info_tokens > 0:
        info = f"{humanize.metric(phase.info_tokens)}_{phase.info_label}"
        parts.append(f"info={info}")
        if phase.info_cached_tokens > 0:
            cache_pct = phase.info_cached_tokens / phase.info_tokens * 100
            parts.append(f"cached={cache_pct:.0f}%")

    if phase.max_workers > 0:
        parts.append(f"workers={phase.active_workers}/{phase.max_workers}")

    parts.append(f"errors={phase.errors}")

    idle = phase.seconds_since_last_ok()
    if idle is not None:
        parts.append(f"idle={int(idle)}s")

    return " ".join(parts)


class HeartbeatLogger:
    """Daemon thread emitting periodic per-phase status lines.

    Cadence is wall-clock so a stalled phase is visibly stalled rather
    than silently quiet. Phases that are still ``pending`` are skipped
    so the operator only sees what's actually running.

    Typical lifecycle:

        hb = HeartbeatLogger(state, interval_seconds=30)
        hb.start()
        try:
            run_pipeline(state)
        finally:
            hb.stop()
    """

    def __init__(
        self,
        state: SyncDisplayState,
        *,
        interval_seconds: float = 30.0,
    ):
        self._state = state
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="sync-heartbeat", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
            self._thread = None
        self._emit_final()

    def _run(self) -> None:
        # First tick after one interval -- lets the pipeline get past
        # startup before we start logging "0 done, 0 total".
        while not self._stop.wait(timeout=self._interval):
            self._emit()

    def _emit(self) -> None:
        for phase in self._state.visible_phases():
            log.info(_format_phase_line(phase))

    def _emit_final(self) -> None:
        """Final summary line per phase that ever ran. Always emitted on
        stop so a short run still leaves a record in the log."""
        for phase in self._state.phases:
            if phase.status == "pending":
                continue
            log.info("final " + _format_phase_line(phase))
