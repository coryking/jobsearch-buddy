"""Typed events for the sync pipeline.

Phases push frozen event dataclasses onto an EventQueue. The CLI (or any
consumer) drains the queue on a separate thread. Adding a new event type
means adding a dataclass here and handling it in the consumer — no changes
to phase signatures.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from enum import Enum, auto


# ---------------------------------------------------------------------------
# Phase discriminator
# ---------------------------------------------------------------------------


class Phase(Enum):
    FETCH = auto()
    ENRICH = auto()
    STRIP = auto()
    EMBED = auto()


# ---------------------------------------------------------------------------
# Event base
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncEvent:
    """Base class for all sync pipeline events."""


# ---------------------------------------------------------------------------
# Fetch events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchStarted(SyncEvent):
    slug: str


@dataclass(frozen=True, slots=True)
class FetchProgress(SyncEvent):
    slug: str
    fetched: int
    total: int


@dataclass(frozen=True, slots=True)
class FetchResult(SyncEvent):
    result: SyncResult


@dataclass(frozen=True, slots=True)
class CompanySkipped(SyncEvent):
    slug: str
    reason: str


# ---------------------------------------------------------------------------
# Generic phase lifecycle events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhaseStarted(SyncEvent):
    phase: Phase
    total: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PhaseProgress(SyncEvent):
    phase: Phase
    done: int
    total: int


@dataclass(frozen=True, slots=True)
class PhaseDone(SyncEvent):
    phase: Phase


@dataclass(frozen=True, slots=True)
class PhaseError(SyncEvent):
    phase: Phase
    job_id: str
    title: str
    error: str



# ---------------------------------------------------------------------------
# Retry visibility
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryEvent(SyncEvent):
    phase: Phase
    slug: str
    job_id: str
    attempt: int
    max_attempts: int
    wait_seconds: float
    reason: str


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Done(SyncEvent):
    """Sentinel: consumer should exit its loop."""


# ---------------------------------------------------------------------------
# Queue alias
# ---------------------------------------------------------------------------

EventQueue = queue.SimpleQueue[SyncEvent]


# ---------------------------------------------------------------------------
# SyncResult (unchanged — used by FetchResult event and orchestrator return)
# ---------------------------------------------------------------------------


class SyncResult:
    """Result from syncing a single company."""

    __slots__ = ("slug", "job_count", "error", "elapsed")

    def __init__(self, slug: str, job_count: int = 0, error: str | None = None, elapsed: float = 0.0):
        self.slug = slug
        self.job_count = job_count
        self.error = error
        self.elapsed = elapsed

    @property
    def ok(self) -> bool:
        return self.error is None
