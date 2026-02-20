"""WorkerPhase -- base class for DB-polling, thread-pooled sync phases."""

from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")

from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState

log = logging.getLogger(__name__)


class WriteQueue:
    """Single-threaded DB writer. Workers submit callables; one connection
    executes them all. Eliminates per-thread connections, file descriptor
    exhaustion, and zombie lock issues at high worker counts.
    """

    def __init__(self, db_path: str):
        self._queue: queue.Queue[Callable[[JobStore], None] | None] = queue.Queue()
        self._db_path = db_path
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        store = JobStore(self._db_path)
        try:
            while True:
                fn = self._queue.get()
                if fn is None:
                    self._queue.task_done()
                    break
                try:
                    fn(store)
                except Exception as e:
                    log.warning("WriteQueue error: %s", e)
                self._queue.task_done()
        finally:
            store.close()

    def submit(self, fn: Callable[[JobStore], None]) -> None:
        self._queue.put(fn)

    def flush(self) -> None:
        """Block until all pending writes are committed."""
        self._queue.join()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join()


class WorkerPhase(ABC, Generic[T]):
    """Subclasses implement: count_remaining(), poll_work(), process_item().

    Base handles: poll loop, ThreadPoolExecutor, display updates, shutdown.
    DB writes go through a single-threaded WriteQueue via submit_write().
    Type parameter T is the work item type (e.g. StripWorkItem, EmbedBatch).
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
        self._writer: WriteQueue | None = None

    def _get_reader(self) -> JobStore:
        """Lazy reader connection — created on the thread that calls run()."""
        if self._reader is None:
            self._reader = JobStore(self.db_path)
        return self._reader

    def submit_write(self, fn: Callable[[JobStore], None]) -> None:
        """Submit a DB write to the single-threaded writer."""
        if self._writer is None:
            raise RuntimeError("WriteQueue not started")
        self._writer.submit(fn)

    @abstractmethod
    def count_remaining(self) -> int: ...

    @abstractmethod
    def poll_work(self, batch_size: int) -> list[T]: ...

    @abstractmethod
    def process_item(self, item: T) -> None: ...

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
        self.display.max_workers = self.max_workers
        self.on_phase_start()

        self._writer = WriteQueue(self.db_path)
        self._writer.start()

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

                    # Flush writes so poll_work sees committed data
                    self._writer.flush()

                    # Update total as upstream produces more work
                    if self._upstream_done:
                        new_total = self.count_remaining() + self.display.done
                        if new_total > self.display.total:
                            self.display.total = new_total
        finally:
            if self._writer:
                self._writer.flush()
                self._writer.stop()
            self.on_phase_end()
            if self._reader is not None:
                self._reader.close()
            self.display.finish()

    def shutdown(self) -> None:
        self._shutdown.set()

    def _safe_process(self, item: T) -> None:
        self.display.active_workers += 1
        try:
            self.process_item(item)
        except Exception as e:
            log.warning("Error in %s: %s", type(self).__name__, e)
            self.display.record_error()
        finally:
            self.display.active_workers -= 1
