"""EmbedPhase -- single-threaded embedding generation.

Embed is intentionally single-threaded. `embed_texts()` paces itself with
the `x-ratelimit-remaining-tokens` header (see embeddings.py), which is
enough to saturate the provider TPM quota from one worker. Running wider
was the source of a prefetch/dedupe race that re-embedded jobs several
times per run.

Mechanism of the old bug (for the next person tempted to re-parallelize):

  1. `JobStore.list_jobs_needing_embeddings()` has no `ORDER BY`. Under
     `LIMIT N`, Postgres is free to return any N matching rows in any
     order, and does.
  2. `WorkerPhase.run()` over-fetched (`poll_limit = batch_size + len(dispatched)`)
     so polls taken while a worker was mid-API-call returned a DIFFERENT
     row set than the batch currently in flight.
  3. The in-memory dedupe set was keyed on the whole-batch tuple
     `tuple(job_ids)`. Different row set → different tuple → not in
     dispatched → re-queued. Every overlapping job got embedded a second
     time (or third, or fourth).

Tuple-keyed dedup was doomed the moment the underlying query was
unordered. This phase runs a plain synchronous loop — poll, embed, write,
repeat — so committed rows leave the `WHERE` clause before the next poll
and the whole race class stops existing.

Trade-off: no intra-phase parallelism. Re-introduce a dispatcher only if
you switch to a provider that can't hit its TPM budget from a single
worker, and:
  - Key dedup on individual job ids, not batch tuples
  - Add `ORDER BY j.id` to `list_jobs_needing_embeddings` for determinism
  - Don't trust the in-memory dedup set alone — let the DB query be the
    source of truth about what still needs work
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import ClassVar

from jobbuddy.embeddings import compute_batch_size, embed_texts
from jobbuddy.models import Job
from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import EmbedBatch, EmbedWorkItem

log = logging.getLogger(__name__)


class EmbedPhase:
    """Generate embeddings for jobs with stripped descriptions.

    Single-threaded by design. See module docstring.
    """

    # Rough accounting for per-call batching within embed_texts.
    MAX_BATCH_TOKENS: ClassVar[int] = 50_000
    CHARS_PER_TOKEN: ClassVar[int] = 4

    # A job that fails this many times in a row is skipped so the phase
    # can finish rather than looping forever on a poisoned record.
    MAX_RETRIES: ClassVar[int] = 3

    # Sleep between empty polls while waiting on upstream producers.
    EMPTY_POLL_WAIT: ClassVar[float] = 1.0

    def __init__(
        self,
        conninfo: str,
        *,
        display: PhaseState,
        slugs: list[str] | None = None,
        upstream_done: threading.Event | None = None,
    ):
        self.conninfo = conninfo
        self.display = display
        self._slugs = slugs
        self._upstream_done = upstream_done
        self._shutdown = threading.Event()
        self._store: JobStore | None = None
        self._batch_size = compute_batch_size()

    @property
    def max_workers(self) -> int:
        """Always 1. Exposed so PhaseState/display code can treat this
        phase the same as the parallel ones."""
        return 1

    def shutdown(self) -> None:
        self._shutdown.set()

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _get_store(self) -> JobStore:
        if self._store is None:
            self._store = JobStore(self.conninfo)
        return self._store

    def _count_remaining(self) -> int:
        return self._get_store().count_jobs_needing_embeddings(slugs=self._slugs)

    def _poll(self, skipped: set[int]) -> list[EmbedWorkItem]:
        """Fetch one batch of work, dropping jobs that hit MAX_RETRIES."""
        # Over-fetch by the skip-set size so we can still fill a batch after
        # filtering out permanently-failing items client-side.
        limit = self._batch_size + len(skipped)
        rows = self._get_store().list_jobs_needing_embeddings(
            slugs=self._slugs, limit=limit,
        )
        return [r for r in rows if r["id"] not in skipped][: self._batch_size]

    def _wait_for_initial_work(self) -> int:
        """Block until upstream produces work or signals done. Returns remaining count."""
        assert self._upstream_done is not None
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=self.EMPTY_POLL_WAIT)
            total = self._count_remaining()
            if total > 0:
                return total
            if self._upstream_done.is_set():
                return self._count_remaining()
        return 0

    # -----------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------

    def run(self) -> None:
        total = self._count_remaining()

        if total == 0 and (
            self._upstream_done is None or self._upstream_done.is_set()
        ):
            log.info("%s phase skipped (nothing to do)", self.display.name)
            if self._store is not None:
                self._store.close()
                self._store = None
            return

        if total == 0 and self._upstream_done is not None:
            total = self._wait_for_initial_work()
            if total == 0:
                log.info("%s phase skipped (nothing to do)", self.display.name)
                if self._store is not None:
                    self._store.close()
                    self._store = None
                return

        log.info("%s phase starting (%d items)", self.display.name, total)
        self.display.start(total)
        self.display.max_workers = self.max_workers

        failures: dict[int, int] = defaultdict(int)
        skipped: set[int] = set()

        try:
            while not self._shutdown.is_set():
                batch = self._poll(skipped)

                if not batch:
                    if (
                        self._upstream_done is not None
                        and not self._upstream_done.is_set()
                    ):
                        self._shutdown.wait(timeout=self.EMPTY_POLL_WAIT)
                        continue
                    break

                self.display.active_workers = 1
                try:
                    self._process_and_isolate(batch, failures, skipped)
                finally:
                    self.display.active_workers = 0

                # Keep the total display honest while upstream is still
                # producing new work.
                if self._upstream_done is not None:
                    new_total = self._count_remaining() + self.display.done
                    if (
                        self.display.total is not None
                        and new_total > self.display.total
                    ):
                        self.display.total = new_total
        finally:
            if self._store is not None:
                self._store.close()
                self._store = None
            self.display.finish()
            log.info(
                "%s phase done (%d done, %d errors)",
                self.display.name, self.display.done, self.display.errors,
            )

    # -----------------------------------------------------------------
    # Batch processing
    # -----------------------------------------------------------------

    def _process_and_isolate(
        self,
        batch: EmbedBatch,
        failures: dict[int, int],
        skipped: set[int],
    ) -> None:
        """Process a batch, bisecting on failure to isolate poison pills.

        Only size-1 leaves charge `failures[]`. When a multi-item batch
        fails, we split it in half and recurse — healthy jobs get embedded
        in the half that doesn't contain the bad record, and only the
        actual culprit accumulates retry count toward MAX_RETRIES.

        Under a fully-transient failure (network down, provider outage),
        every leaf fails and every job gets charged once per outer poll
        iteration, which is the same behavior as a flat batch-level
        failure would give. Worst case: ~2N API calls for an N-item
        batch where every job is bad.
        """
        try:
            self._process_batch(batch)
            return
        except Exception as e:
            self.display.record_error()
            log.warning(
                "Embed batch failed (%d jobs): %s: %s",
                len(batch), type(e).__name__, e,
            )

        if len(batch) == 1:
            job = batch[0]
            jid = job["id"]
            failures[jid] += 1
            if failures[jid] >= self.MAX_RETRIES:
                log.error(
                    "Giving up on embed for %s/%s after %d failures",
                    job["company_slug"], job["job_id"], failures[jid],
                )
                skipped.add(jid)
            return

        mid = len(batch) // 2
        self._process_and_isolate(batch[:mid], failures, skipped)
        self._process_and_isolate(batch[mid:], failures, skipped)

    def _process_batch(self, batch: EmbedBatch) -> None:
        """Embed one batch and write results synchronously.

        Raises on any failure so run() can record an error and drive retry.
        """
        texts: list[str] = []
        valid: list[EmbedWorkItem] = []
        est_tokens = 0

        for j in batch:
            job = Job(
                id=j["job_id"], title=j["title"],
                location=j["location"] or "", url="", apply_url="",
                department=j["department"],
            )
            text = job.embed_text(
                j["company_slug"],
                description_stripped=j["description_stripped"],
            )
            if not text:
                continue
            text_tokens = len(text) // self.CHARS_PER_TOKEN
            if est_tokens + text_tokens > self.MAX_BATCH_TOKENS and texts:
                break
            texts.append(text)
            valid.append(j)
            est_tokens += text_tokens

        if not texts:
            return

        vectors, total_tokens = embed_texts(texts)

        if total_tokens:
            self.display.add_to_info_counter(total_tokens)
            self.display.token_rate.record(total_tokens)

        embed_items = [
            (j["id"], str(j["job_hash"]), str(j["company_hash"]), vec)
            for j, vec in zip(valid, vectors)
        ]
        # Synchronous write — no WriteQueue needed since we're single-threaded.
        self._get_store().store_embeddings(embed_items)

        for job_info in valid:
            self.display.advance(
                detail=f"{job_info['company_slug']}: {job_info['title']}"
            )
