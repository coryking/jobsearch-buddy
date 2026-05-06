"""Phase metrics structs.

Plain counters, locks, rolling windows. No rendering. The sync pipeline
emits its progress through stdlib `logging` and the HeartbeatLogger
(see `sync/heartbeat.py`); these objects exist so phases have a
thread-safe place to record done counts, error counts, token usage,
and worker activity that the heartbeat thread can sample on a timer.

The module is named `display` for backwards compatibility with callers
that still import `PhaseState` / `SyncDisplayState` from here.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal


PhaseStatus = Literal["pending", "active", "idle"]


class RollingRate:
    """Items/min from a 60-second sliding window of timestamps."""

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


class RollingTokenRate:
    """Tokens/min from a 60-second sliding window of (timestamp, count) pairs."""

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._entries: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def record(self, n: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._entries.append((now, n))
            cutoff = now - self._window
            while self._entries and self._entries[0][0] < cutoff:
                self._entries.popleft()

    def tokens_per_min(self) -> float | None:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            while self._entries and self._entries[0][0] < cutoff:
                self._entries.popleft()
            if len(self._entries) < 2:
                return None
            span = now - self._entries[0][0]
            if span < 0.1:
                return None
            total = sum(n for _, n in self._entries)
            return total / span * 60


@dataclass
class PhaseState:
    """Mutable metrics for one pipeline phase.

    Written by phase workers, read by the HeartbeatLogger.
    Simple attribute writes are GIL-atomic. RollingRate has its own lock.

    `errors` counts worker-side exceptions (a row that failed processing
    after retries). WriteQueue-side failures are not counted here --
    they're fatal and crash the whole sync (see `WriteQueue` in
    `sync/base.py`).
    """

    name: str
    status: PhaseStatus = "pending"
    done: int = 0
    total: int | None = None
    errors: int = 0
    last_ok_at: float | None = None  # time.monotonic()
    detail: str = ""  # current activity (company name, job title, etc.)
    info_tokens: int = 0
    info_cached_tokens: int = 0
    info_label: str = "tok"
    rate: RollingRate = field(default_factory=RollingRate)
    token_rate: RollingTokenRate = field(default_factory=RollingTokenRate)
    active_workers: int = 0
    max_workers: int = 0
    _info_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _active_details: dict[str, str] = field(default_factory=dict, repr=False)
    _active_details_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

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

    def set_active_detail(self, key: str, text: str) -> None:
        with self._active_details_lock:
            self._active_details[key] = text

    def remove_active_detail(self, key: str) -> None:
        with self._active_details_lock:
            self._active_details.pop(key, None)

    def get_active_details(self) -> list[str]:
        with self._active_details_lock:
            return [self._active_details[k] for k in sorted(self._active_details)]

    def add_to_info_counter(self, n: int, label: str = "tok", cached: int = 0) -> None:
        """Accumulate phase-specific counter (tokens, jobs fetched, web searches).

        `cached` counts input tokens served from the prefix cache (Azure /
        OpenAI usage.prompt_tokens_details.cached_tokens) when relevant.
        Heartbeat output renders this as a percentage so operators can see
        whether prefix-cache optimization is paying off.
        """
        with self._info_lock:
            self.info_tokens += n
            self.info_cached_tokens += cached
            self.info_label = label.strip() or "tok"

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
    research: PhaseState = field(default_factory=lambda: PhaseState("Research"))
    distill: PhaseState = field(default_factory=lambda: PhaseState("Distill"))

    @property
    def phases(self) -> list[PhaseState]:
        return [self.fetch, self.enrich, self.research, self.distill]

    def visible_phases(self) -> list[PhaseState]:
        return [p for p in self.phases if p.status != "pending"]
