"""EmbedPhase -- embedding generation via OpenAI text-embedding-3-small.

Single model. Batch processing to maximize throughput within API limits.
"""

from __future__ import annotations

import logging
import threading

from jobbuddy.embeddings import compute_batch_size, embed_texts
from jobbuddy.models import Job
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import EmbedBatch

log = logging.getLogger(__name__)


class EmbedPhase(WorkerPhase["EmbedBatch"]):
    """Generate embeddings for jobs with stripped descriptions.

    Work unit = a batch of jobs (list of EmbedWorkItems from store).
    Each batch is embedded in a single API call, then results are stored individually.
    """

    def __init__(self, conninfo: str, *, display: PhaseState,
                 max_workers: int = 1, slugs: list[str] | None = None,
                 upstream_done: threading.Event | None = None):
        super().__init__(conninfo, max_workers=max_workers, display=display,
                         upstream_done=upstream_done)
        self._batch_size = compute_batch_size()
        self._slugs = slugs

    @property
    def batch_size(self) -> int:
        """Override: use embedding-optimal batch size, not worker * 2."""
        return self._batch_size

    def item_key(self, item: EmbedBatch) -> tuple:
        return tuple(j["id"] for j in item)

    def item_label(self, item: EmbedBatch) -> str:
        slugs = {j["company_slug"] for j in item}
        return f"batch of {len(item)} jobs ({', '.join(sorted(slugs))})"

    def count_remaining(self) -> int:
        return self._get_reader().count_jobs_needing_embeddings(slugs=self._slugs)

    def poll_work(self, batch_size: int) -> list[EmbedBatch]:
        """Return a single batch of jobs as a one-element list (one work unit)."""
        jobs = self._get_reader().list_jobs_needing_embeddings(slugs=self._slugs, limit=batch_size)
        if not jobs:
            return []
        return [jobs]

    # Target ~50K tokens per batch for predictable pacing.
    # ~4 chars per token is a rough estimate.
    MAX_BATCH_TOKENS = 50_000
    CHARS_PER_TOKEN = 4

    def process_item(self, item: EmbedBatch) -> None:
        """Embed a batch of jobs and store results."""
        jobs = item
        texts = []
        valid_jobs = []
        est_tokens = 0
        for j in jobs:
            job = Job(
                id=j["job_id"], title=j["title"],
                location=j["location"] or "", url="", apply_url="",
                department=j["department"],
            )
            text = job.embed_text(j["company_slug"], description_stripped=j["description_stripped"])
            if text:
                text_tokens = len(text) // self.CHARS_PER_TOKEN
                if est_tokens + text_tokens > self.MAX_BATCH_TOKENS and texts:
                    break
                texts.append(text)
                valid_jobs.append(j)
                est_tokens += text_tokens

        if not texts:
            return

        try:
            vectors, total_tokens = embed_texts(texts)
        except Exception as e:
            log.warning("Embedding batch failed (%d jobs): %s", len(valid_jobs), e)
            raise

        if total_tokens:
            self.display.add_to_info_counter(total_tokens)
            self.display.token_rate.record(total_tokens)

        embed_items = [
            (j["id"], str(j["job_hash"]), str(j["company_hash"]), vec)
            for j, vec in zip(valid_jobs, vectors)
        ]
        self.submit_write(
            lambda store, items=embed_items: store.store_embeddings(items)
        )
        for job_info in valid_jobs:
            self.display.advance(
                detail=f"{job_info['company_slug']}: {job_info['title']}"
            )
