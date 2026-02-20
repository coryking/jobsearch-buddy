"""Rich Live TUI display for the sync pipeline.

Phases update PhaseState objects directly; the Rich Live renderer
polls them at 4hz. No event queue needed for display.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
import humanize


PhaseStatus = Literal["pending", "active", "idle"]


class RollingRate:
    """Items/min from a 60-second sliding window of timestamps.

    Why timestamps not a counter: counter gives cumulative average which
    hides stalls. Timestamps show rate dropping to zero when nothing completes.
    """
    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def record(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._timestamps.append(now)
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

    def rate_per_min(self) -> float | None:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) < 2:
                return None
            span = now - self._timestamps[0]
            if span < 0.1:
                return None
            return len(self._timestamps) / span * 60


@dataclass
class PhaseState:
    """Mutable display state for one pipeline phase.

    Written by phase workers, read by Rich Live renderer.
    Simple attribute writes are GIL-atomic. RollingRate has its own lock.
    """
    name: str
    status: PhaseStatus = "pending"
    done: int = 0
    total: int | None = None
    errors: int = 0
    last_ok_at: float | None = None  # time.monotonic()
    detail: str = ""  # sub-heading: company names, etc.
    info: str = ""  # phase-controlled column (tokens, jobs, etc.)
    rate: RollingRate = field(default_factory=RollingRate)
    _info_counter: int = field(default=0, repr=False)
    _info_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self, total: int, detail: str = "") -> None:
        self.status = "active"
        self.total = total
        self.detail = detail

    def advance(self, detail: str | None = None) -> None:
        self.done += 1
        self.last_ok_at = time.monotonic()
        self.rate.record()
        if detail is not None:
            self.detail = detail

    def add_to_info_counter(self, n: int, label: str = "tok") -> None:
        """Thread-safe accumulator. Updates the info display string."""
        with self._info_lock:
            self._info_counter += n
            self.info = f"{humanize.metric(self._info_counter, '.1f')}{label}"

    def record_error(self) -> None:
        self.errors += 1

    def finish(self) -> None:
        self.status = "idle"

    def seconds_since_last_ok(self) -> float | None:
        if self.last_ok_at is None:
            return None
        return time.monotonic() - self.last_ok_at


@dataclass
class SyncDisplayState:
    """All phase states. Created once, shared across threads."""
    fetch: PhaseState = field(default_factory=lambda: PhaseState("Fetch"))
    enrich: PhaseState = field(default_factory=lambda: PhaseState("Enrich"))
    strip: PhaseState = field(default_factory=lambda: PhaseState("Strip"))
    embed: PhaseState = field(default_factory=lambda: PhaseState("Embed"))

    @property
    def phases(self) -> list[PhaseState]:
        return [self.fetch, self.enrich, self.strip, self.embed]

    def visible_phases(self) -> list[PhaseState]:
        return [p for p in self.phases if p.status != "pending"]


def _format_progress(phase: PhaseState) -> str:
    """Format progress column: 'done/total' or just 'done'."""
    done_str = humanize.intcomma(phase.done)
    if phase.total is not None:
        return f"{done_str}/{humanize.intcomma(phase.total)}"
    return done_str


def _format_rate(phase: PhaseState) -> str:
    """Format rate column: 'N/m' or dash."""
    rpm = phase.rate.rate_per_min()
    if rpm is None:
        return "\u2014"
    return f"{humanize.intcomma(int(rpm))}/m"


def _format_last_ok(phase: PhaseState) -> str:
    """Format last-ok column: 'Ns' or dash."""
    elapsed = phase.seconds_since_last_ok()
    if elapsed is None:
        return "\u2014"
    return f"{int(elapsed)}s"


def _format_errors(phase: PhaseState) -> str:
    """Format error count, red if > 0, dash if 0."""
    if phase.errors > 0:
        return f"[red]{humanize.intcomma(phase.errors)}[/red]"
    return "\u2014"


def _format_status(phase: PhaseState) -> str:
    """Format status with color."""
    if phase.status == "active":
        return "[bold green]active[/bold green]"
    elif phase.status == "idle":
        return "[dim]idle[/dim]"
    return "[dim]pending[/dim]"


def _suffix_for_phase(phase: PhaseState) -> str:
    """Unit suffix for the done count when phase is idle with no total."""
    if phase.name == "Fetch" and phase.status == "idle" and phase.total is None:
        return " co."
    return ""


def build_display(state: SyncDisplayState, filter_phases: list[str] | None = None) -> Group:
    """Pure function: read state, return Rich renderable.

    Columns: Phase | Status | Progress | Rate | Err | Last OK
    Active phases get a sub-heading row showing detail text.
    """
    phases = state.visible_phases()
    if filter_phases:
        phases = [p for p in phases if p.name in filter_phases]

    if not phases:
        return Group(Text("  Waiting for phases to start...", style="dim"))

    table = Table(
        title="ats sync",
        show_header=True,
        show_lines=False,
        pad_edge=True,
        box=None,
        title_style="bold",
        header_style="dim",
    )
    table.add_column("Phase", style="bold", no_wrap=True, min_width=10)
    table.add_column("Status", no_wrap=True, min_width=10)
    table.add_column("Progress", justify="right", no_wrap=True, min_width=12)
    table.add_column("Rate", justify="right", no_wrap=True, min_width=7)
    table.add_column("Info", justify="right", no_wrap=True, min_width=8)
    table.add_column("Err", justify="right", no_wrap=True, min_width=5)

    for phase in phases:
        progress = _format_progress(phase) + _suffix_for_phase(phase)
        style = "dim" if phase.status == "idle" else ""

        table.add_row(
            phase.name,
            _format_status(phase),
            progress,
            _format_rate(phase),
            phase.info or "\u2014",
            _format_errors(phase),
            style=style,
        )

        # Sub-heading row for active phases with detail text
        if phase.status == "active" and phase.detail:
            table.add_row(
                "",
                Text(f" {phase.detail}", style="dim italic"),
                "", "", "", "",
            )

    return Group(table)


class SyncRenderable:
    """Wrapper so Rich Live calls build_display() on every render cycle."""

    def __init__(self, state: SyncDisplayState, filter_phases: list[str] | None = None):
        self.state = state
        self.filter_phases = filter_phases

    def __rich__(self) -> Group:
        return build_display(self.state, self.filter_phases)


def create_live(console: Console, state: SyncDisplayState,
                filter_phases: list[str] | None = None) -> Live:
    """4hz refresh. For standalone commands, pass filter_phases=["Strip"] etc."""
    return Live(
        SyncRenderable(state, filter_phases),
        console=console,
        refresh_per_second=4,
    )


def print_sync_summary(console: Console, state: SyncDisplayState) -> None:
    """Print final stats after sync completes."""
    console.print()
    console.rule("[bold]Sync Summary[/bold]")

    table = Table(show_lines=False, pad_edge=False, box=None)
    table.add_column("Phase", style="bold", no_wrap=True)
    table.add_column("Done", justify="right")
    table.add_column("Errors", justify="right")

    total_done = 0
    total_errors = 0

    for phase in state.phases:
        # Skip phases that never ran
        if phase.status == "pending":
            continue

        total_done += phase.done
        total_errors += phase.errors

        err_str = (
            f"[red]{humanize.intcomma(phase.errors)}[/red]"
            if phase.errors > 0
            else "[green]0[/green]"
        )

        table.add_row(
            phase.name,
            humanize.intcomma(phase.done),
            err_str,
        )

    console.print(table)

    if total_errors:
        console.print(
            f"\n  [red]{humanize.intcomma(total_errors)} total errors[/red]"
        )
    else:
        console.print(
            f"\n  [green]{humanize.intcomma(total_done)} items processed, no errors[/green]"
        )
