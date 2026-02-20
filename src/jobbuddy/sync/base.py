"""WorkerPhase -- base class for DB-polling, thread-pooled sync phases."""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState

log = logging.getLogger(__name__)


class WorkerPhase(ABC):
    """Subclasses implement: count_remaining(), poll_work(), process_item().

    Base handles: poll loop, ThreadPoolExecutor, display updates, shutdown.
    Each worker thread gets its own DB connection via _get_thread_store().
    """

    def __init__(self, db_path: str | Path, *, max_workers: int,
                 display: PhaseState,
                 upstream_done: threading.Event | None = None):
        self.db_path = str(db_path)
        self.max_workers = max_workers
        self.display = display
        self._shutdown = threading.Event()
        self._upstream_done = upstream_done
        self._reader: JobStore | None = None  # created lazily in run() thread
        self._local = threading.local()   # per-worker stores

    def _get_reader(self) -> JobStore:
        """Lazy reader connection — created on the thread that calls run()."""
        if self._reader is None:
            self._reader = JobStore(self.db_path)
        return self._reader

    @abstractmethod
    def count_remaining(self) -> int: ...

    @abstractmethod
    def poll_work(self, batch_size: int) -> list[Any]: ...

    @abstractmethod
    def process_item(self, item: Any) -> None: ...

    def on_phase_start(self) -> None: pass
    def on_phase_end(self) -> None: pass

    @property
    def batch_size(self) -> int:
        return self.max_workers * 2

    def run(self) -> None:
        total = self.count_remaining()

        if total == 0 and not self._upstream_done:
            return
        if total == 0 and self._upstream_done and self._upstream_done.is_set():
            return

        # If upstream is still running and we have no work yet, wait for it
        if total == 0 and self._upstream_done:
            while not self._shutdown.is_set():
                self._shutdown.wait(timeout=1.0)
                total = self.count_remaining()
                if total > 0:
                    break
                if self._upstream_done.is_set():
                    total = self.count_remaining()
                    if total == 0:
                        return
                    break

        self.display.start(total)
        self.on_phase_start()

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                while not self._shutdown.is_set():
                    batch = self.poll_work(self.batch_size)
                    if not batch:
                        # If upstream is still producing, wait and retry
                        if self._upstream_done and not self._upstream_done.is_set():
                            self._shutdown.wait(timeout=1.0)
                            continue
                        break
                    futures = {
                        executor.submit(self._safe_process, item): item
                        for item in batch
                    }
                    for future in as_completed(futures):
                        if self._shutdown.is_set():
                            for f in futures:
                                f.cancel()
                            break
                        future.result()  # propagate unexpected errors

                    # Update total as upstream produces more work
                    if self._upstream_done:
                        new_total = self.count_remaining() + self.display.done
                        if new_total > self.display.total:
                            self.display.total = new_total
        finally:
            self.on_phase_end()
            if self._reader is not None:
                self._reader.close()
            self.display.finish()

    def shutdown(self) -> None:
        self._shutdown.set()

    def _safe_process(self, item: Any) -> None:
        try:
            self.process_item(item)
        except Exception as e:
            log.warning("Error in %s: %s", type(self).__name__, e)
            self.display.record_error()

    def _get_thread_store(self) -> JobStore:
        if not hasattr(self._local, "store"):
            self._local.store = JobStore(self.db_path)
        return self._local.store
