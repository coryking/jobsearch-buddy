"""Result types for the sync pipeline."""

from __future__ import annotations


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
