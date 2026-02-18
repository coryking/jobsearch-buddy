"""Shared types for the sync pipeline."""

from collections.abc import Callable
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Callback types
# ---------------------------------------------------------------------------

type StartCallback = Callable[[str], None]
type SkipCallback = Callable[[str, str], None]
type FetchProgressCallback = Callable[[str, int, int], None]
type CountCallback = Callable[[int], None]
type ProgressCallback = Callable[[int, int], None]
type DoneCallback = Callable[[], None]
type EmbedStartCallback = Callable[[int, str, int], None]  # (total, model_name, dimensions)
type ModelEventCallback = Callable[[str, str, str], None]  # (model_key, model_name, device)


@dataclass
class SyncCallbacks:
    """Typed container for sync pipeline callbacks."""

    on_start: StartCallback | None = None
    on_result: "Callable[[SyncResult], None] | None" = None
    on_skip: SkipCallback | None = None
    on_fetch_progress: FetchProgressCallback | None = None
    on_enrich_start: CountCallback | None = None
    on_enrich_progress: ProgressCallback | None = None
    on_enrich_done: DoneCallback | None = None
    on_embed_start: EmbedStartCallback | None = None
    on_embed_progress: ProgressCallback | None = None
    on_embed_done: DoneCallback | None = None
    on_model_load: ModelEventCallback | None = None
    on_model_unload: ModelEventCallback | None = None


# ---------------------------------------------------------------------------
# SyncResult
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
