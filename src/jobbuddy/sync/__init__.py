"""Sync pipeline -- public surface.

Implementation lives in `orchestrator.py` and per-phase modules
(`fetch.py`, `enrich.py`, `strip.py`, `embed.py`, `research.py`)."""

from jobbuddy.sync.orchestrator import (
    VALID_PHASES,
    SyncConfig,
    sync_jobs,
    validate_sync_config,
)
from jobbuddy.sync.types import SyncResult

__all__ = [
    "SyncConfig",
    "SyncResult",
    "VALID_PHASES",
    "sync_jobs",
    "validate_sync_config",
]
