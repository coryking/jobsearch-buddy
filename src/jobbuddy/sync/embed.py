"""EmbedPhase -- embedding generation via Azure OpenAI text-embedding-3-small.

Single model. Batch processing to maximize throughput within API limits.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jobbuddy.embeddings import compute_batch_size, embed_texts, serialize_f32
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState

log = logging.getLogger(__name__)


class EmbedPhase(WorkerPhase):
    """Generate embeddings for jobs with stripped descriptions.

    Work unit = a batch of jobs (list of dicts from store.jobs_needing_embeddings).
    Each batch is embedded in a single API call, then results are stored individually.
    """

    def __init__(self, db_path: str | Path, *, display: PhaseState,
                 max_workers: int = 4):
        super().__init__(db_path, max_workers=max_workers, display=display)
        self._batch_size = compute_batch_size()

    @property
    def batch_size(self) -> int:
        """Override: use embedding-optimal batch size, not worker * 2."""
        return self._batch_size

    def count_remaining(self) -> int:
        return self._reader.jobs_needing_embeddings(count_only=True)

    def poll_work(self, batch_size: int) -> list[list[dict]]:
        """Return a single batch of jobs as a one-element list (one work unit)."""
        jobs = self._reader.jobs_needing_embeddings(limit=batch_size)
        if not jobs:
            return []
        return [jobs]  # one work unit = one batch

    def process_item(self, item: list[dict]) -> None:
        """Embed a batch of jobs and store results."""
        jobs = item
        texts = [j["text"] for j in jobs]

        # Collect unique company slugs for display detail
        slugs = list(dict.fromkeys(j["company_slug"] for j in jobs))
        detail = ", ".join(slugs[:5])
        if len(slugs) > 5:
            detail += f" +{len(slugs) - 5}"

        try:
            vectors = embed_texts(texts)
        except Exception as e:
            log.warning("Embedding batch failed (%d jobs): %s", len(jobs), e)
            raise

        store = self._get_thread_store()
        for job_info, vec in zip(jobs, vectors):
            blob = serialize_f32(vec)
            store.store_embedding(
                job_info["id"],
                blob,
                job_info["text_hash"],
            )
            self.display.advance(detail=detail)
