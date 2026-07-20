"""WorkerPhase -- base class for DB-polling, thread-pooled sync phases.

Architecture: producer-consumer with a single-threaded DB writer.

    Producer (run thread)          Workers (ThreadPool)         Writer (single thread)
    +------------------+            +--------------+            +--------------+
    | poll DB          |--items-->  | work_queue   |--process-->| write_queue   |
    | filter dispatched|            | (bounded)    |  submit_   | (unbounded)  |
    | flush + re-poll  |            |              |  write()   |              |
    +------------------+            +--------------+            +--------------+

Workers pull from work_queue continuously -- no batch boundaries, no idle time.
The dispatched set (in-memory) tracks items already queued so re-polls after
flush don't duplicate in-flight work. Safe on crash: items are still NULL in
the DB and get picked up on the next run.
"""

from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar, Generic, TypeVar

import psycopg

T = TypeVar("T")
R = TypeVar("R")

from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState

log = logging.getLogger(__name__)


class PhaseCostLimitExceeded(RuntimeError):
    """A phase hit its per-run spend ceiling (`max_cost_usd`) and refused to
    start more paid work. Raised out of run() so the sync exits non-zero —
    the ceiling is a hard budget, not advice."""


def _is_connection_dead(store: JobStore | None, exc: BaseException) -> bool:
    """Heuristic: did this exception come from a dead/closed connection?

    Azure Entra tokens expire around 1h; the resulting failures show up as
    psycopg.OperationalError / InterfaceError, or as "the connection is
    closed" from psycopg itself. We also treat a `conn.closed` flag as a
    signal regardless of the exception type.
    """
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    if store is not None:
        conn = getattr(store, "conn", None)
        if conn is not None and getattr(conn, "closed", False):
            return True
    return False


class WriteQueue:
    """Single-threaded DB writer. Workers submit callables; one connection
    executes them all. Eliminates per-thread connections, file descriptor
    exhaustion, and zombie lock issues at high worker counts.

    On connection-level failures (closed connection, expired Entra token,
    server-side reset) the writer transparently reconnects via the supplied
    `conninfo_factory` and retries the failed callable exactly once.

    Any other failure -- including a retry-after-reconnect that still
    fails -- is fatal. The writer logs the traceback, marks itself fatal,
    drains the queue without executing, and the next `submit()` / `flush()`
    re-raises. The owning phase propagates the exception out of `run()`,
    which kills the whole sync process. The upstream LLM/HTTP call that
    produced the row was already paid for, so silently dropping the row
    would mean burning money for nothing AND leaving stale NULLs that
    look identical to "never processed". Termination is louder, cheaper,
    and easier for an operator (or LLM) to investigate.
    """

    def __init__(self, conninfo_factory: Callable[[], str]):
        self._queue: queue.Queue[Callable[[JobStore], None] | None] = queue.Queue()
        self._conninfo_factory = conninfo_factory
        self._thread: threading.Thread | None = None
        self._fatal: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _open_store(self) -> JobStore:
        return JobStore(self._conninfo_factory())

    def _bail(self, exc: BaseException) -> None:
        """Mark the queue fatal and drain remaining items without executing.

        Drain ensures flush() (which calls _queue.join()) returns instead of
        hanging on task_done counts. The fatal exception is surfaced to the
        caller from submit() and flush().
        """
        log.error("WriteQueue fatal: %s; bailing", exc, exc_info=exc)
        self._fatal = exc
        try:
            while True:
                item = self._queue.get_nowait()
                self._queue.task_done()
                if item is None:
                    break
        except queue.Empty:
            pass

    def _run(self) -> None:
        try:
            store = self._open_store()
        except Exception as e:
            self._bail(e)
            return
        try:
            while True:
                fn = self._queue.get()
                if fn is None:
                    self._queue.task_done()
                    break
                try:
                    fn(store)
                except Exception as e:
                    if _is_connection_dead(store, e):
                        log.warning(
                            "WriteQueue connection dead (%s); reconnecting", e
                        )
                        try:
                            store.close()
                        except Exception:
                            pass
                        try:
                            store = self._open_store()
                        except Exception as reopen_err:
                            # _bail BEFORE task_done: flush() unblocks on the
                            # task_done count, so _fatal must already be
                            # visible when it does — the reverse order let a
                            # single flush() race past the error unseen.
                            self._bail(reopen_err)
                            self._queue.task_done()
                            return
                        try:
                            fn(store)
                        except Exception as retry_err:
                            self._bail(retry_err)
                            self._queue.task_done()
                            return
                    else:
                        # Per-row write error. We refuse to drop the row --
                        # the upstream call was paid for. Bail and let the
                        # operator see exactly what failed.
                        self._bail(e)
                        self._queue.task_done()
                        return
                self._queue.task_done()
        finally:
            try:
                store.close()
            except Exception:
                pass

    @property
    def is_fatal(self) -> bool:
        """True once the writer has bailed. Phases check this BEFORE starting
        paid work — a dead write path means any LLM/HTTP spend is wasted."""
        return self._fatal is not None

    def submit(self, fn: Callable[[JobStore], None]) -> None:
        if self._fatal is not None:
            raise RuntimeError(
                f"WriteQueue is fatal; cannot accept writes: {self._fatal}"
            ) from self._fatal
        self._queue.put(fn)

    def flush(self) -> None:
        """Block until all pending writes are committed.

        Re-raises the fatal exception if the writer bailed on a dead
        connection it couldn't recover from.
        """
        self._queue.join()
        if self._fatal is not None:
            raise RuntimeError(
                f"WriteQueue bailed: {self._fatal}"
            ) from self._fatal

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join()


class WorkerPhase(ABC, Generic[T]):
    """Subclasses implement: count_remaining(), poll_work(), process_item(), item_key().

    Base handles: producer-consumer loop, WriteQueue, display updates, shutdown.
    DB writes go through a single-threaded WriteQueue via submit_write().
    Type parameter T is the work item type (e.g. EnrichWorkItem).
    """

    def __init__(self, conninfo: str, *, max_workers: int,
                 display: PhaseState,
                 upstream_done: threading.Event | None = None,
                 conninfo_factory: Callable[[], str] | None = None,
                 max_cost_usd: float | None = None):
        self.conninfo = conninfo
        # Factory is invoked at WriteQueue startup AND on every reconnect, so
        # each reconnect picks up a fresh Entra token in Azure mode. Default
        # closes over the static conninfo for callers that don't need refresh
        # (local password auth, tests).
        self._conninfo_factory: Callable[[], str] = (
            conninfo_factory if conninfo_factory is not None
            else (lambda: self.conninfo)
        )
        self.max_workers = max_workers
        self.display = display
        # Hard per-run spend ceiling for phases that pay per item (LLM
        # calls). None = no ceiling. Checked against display.info_cost_usd.
        self.max_cost_usd = max_cost_usd
        self._shutdown = threading.Event()
        self._upstream_done = upstream_done
        self._reader: JobStore | None = None
        self._writer: WriteQueue | None = None

    def _get_reader(self) -> JobStore:
        """Lazy reader connection -- created on the thread that calls run().

        Uses the conninfo factory so the reader picks up a fresh Entra token
        on first open (the static `self.conninfo` captured at sync startup is
        stale by the time long-running phases reach distill). Recycles the
        reader if its connection is closed, so a mid-run token expiry
        recovers on the next query rather than crashing the phase.
        """
        if self._reader is not None:
            conn = getattr(self._reader, "conn", None)
            if conn is None or getattr(conn, "closed", False):
                try:
                    self._reader.close()
                except Exception:
                    pass
                self._reader = None
        if self._reader is None:
            self._reader = JobStore(self._conninfo_factory())
        return self._reader

    def _run_reader_query(self, fn: Callable[[JobStore], R]) -> R:
        """Run a reader query, reconnecting once on dead-connection errors.

        Mirrors the WriteQueue's reconnect-on-stale-token behavior so reader
        queries (count_remaining, poll_work) survive Entra token expiry that
        happens mid-phase rather than crashing the whole sync.
        """
        reader = self._get_reader()
        try:
            return fn(reader)
        except Exception as e:
            if not _is_connection_dead(reader, e):
                raise
            log.warning("Reader connection dead (%s); reconnecting", e)
            try:
                reader.close()
            except Exception:
                pass
            self._reader = None
            reader = self._get_reader()
            return fn(reader)

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

    @abstractmethod
    def item_key(self, item: T) -> Hashable:
        """Return a key identifying this work item for deduplication."""
        ...

    def item_label(self, item: T) -> str:
        """Human-readable label for error messages. Override in subclasses."""
        return str(self.item_key(item))

    def on_phase_start(self) -> None: pass
    def on_phase_end(self) -> None: pass

    @property
    def batch_size(self) -> int:
        return self.max_workers * 2

    def run(self) -> None:
        total = self.count_remaining()

        if total == 0 and not self._upstream_done:
            log.info("%s phase skipped (nothing to do)", self.display.name)
            return
        if total == 0 and self._upstream_done and self._upstream_done.is_set():
            log.info("%s phase skipped (nothing to do)", self.display.name)
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
                        log.info("%s phase skipped (nothing to do)", self.display.name)
                        return
                    break

        log.info("%s phase starting (%d items)", self.display.name, total)
        self.display.start(total)
        self.display.max_workers = self.max_workers
        self.on_phase_start()

        self._writer = WriteQueue(conninfo_factory=self._conninfo_factory)
        self._writer.start()

        work_queue: queue.Queue[T | None] = queue.Queue(maxsize=self.max_workers * 2)
        dispatched: set[Hashable] = set()
        failures: dict[Hashable, int] = {}

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Start persistent worker threads
                worker_futures = [
                    executor.submit(self._worker_loop, work_queue, dispatched, failures)
                    for _ in range(self.max_workers)
                ]

                try:
                    # Producer loop (this thread)
                    while not self._shutdown.is_set():
                        # Flush writes so DB reflects completed work, then poll
                        self._writer.flush()
                        self._check_cost_limit()
                        # Over-fetch past in-flight items that are still NULL
                        poll_limit = self.batch_size + len(dispatched)
                        items = self.poll_work(poll_limit)
                        new_items = [i for i in items if self.item_key(i) not in dispatched]

                        if not new_items:
                            if self._upstream_done and not self._upstream_done.is_set():
                                self._shutdown.wait(timeout=1.0)
                                continue
                            break

                        for item in new_items:
                            dispatched.add(self.item_key(item))
                            # put() with timeout so we can respond to shutdown
                            while not self._shutdown.is_set():
                                try:
                                    work_queue.put(item, timeout=0.5)
                                    break
                                except queue.Full:
                                    continue

                        # Update total as upstream produces more work
                        if self._upstream_done:
                            self._writer.flush()
                            new_total = self.count_remaining() + self.display.done
                            if self.display.total is not None and new_total > self.display.total:
                                self.display.total = new_total
                except BaseException:
                    # Freeze the workers before releasing them: on a
                    # producer failure the items still in the queue must
                    # be skipped, not processed — process_item is where
                    # money is spent.
                    self._shutdown.set()
                    raise
                finally:
                    # ALWAYS send the worker sentinels. When the producer
                    # loop raises (fatal writer, cost limit), skipping the
                    # sends leaves max_workers threads parked on
                    # work_queue.get() and ThreadPoolExecutor.__exit__
                    # joins them forever — the 2026-07-10 sync hung for
                    # 10 days in exactly that state. On the normal path
                    # the sentinels queue behind remaining items and the
                    # workers finish them first; on the abort path the
                    # shutdown flag makes those items fast skips, so
                    # these puts can't block on a full queue for long.
                    for _ in range(self.max_workers):
                        work_queue.put(None)

                for f in worker_futures:
                    f.result()
                # Post-loop checks: workers may have recorded spend (or the
                # writer may have gone fatal) after the producer's last
                # in-loop check — e.g. when every item was dispatched in the
                # first few polls and the loop exited before any completed.
                self._writer.flush()
                self._check_cost_limit()
        finally:
            try:
                if self._writer:
                    try:
                        # Final flush: on the normal path this surfaces a
                        # late writer failure; on the exception path it
                        # re-raises the same fatal error that's already
                        # propagating (harmless). Nested finally keeps
                        # stop() and the rest of teardown running either way.
                        self._writer.flush()
                    finally:
                        self._writer.stop()
            finally:
                self.on_phase_end()
                if self._reader is not None:
                    self._reader.close()
                self.display.finish()
                log.info("%s phase done (%d done, %d errors)",
                         self.display.name, self.display.done, self.display.errors)

    def shutdown(self) -> None:
        self._shutdown.set()

    MAX_RETRIES: ClassVar[int] = 3

    def _worker_loop(self, work_queue: queue.Queue[T | None],
                     dispatched: set[Hashable],
                     failures: dict[Hashable, int]) -> None:
        """Persistent worker: pull items from queue until sentinel."""
        while True:
            item = work_queue.get()
            if item is None:
                work_queue.task_done()
                break
            try:
                self._safe_process(item, dispatched, failures)
            finally:
                work_queue.task_done()

    def _check_cost_limit(self) -> None:
        """Raise if this phase has spent past its per-run budget.

        The ceiling is a hard bound on unattended spend: a cron-driven
        phase that pays per item must not be able to burn past a number
        the operator chose, no matter what the backlog or a retry loop
        looks like. Raise the limit deliberately (settings /
        JOBBUDDY_SYNC_MAX_PHASE_COST_USD) for a known-big run.
        """
        if self.max_cost_usd is None:
            return
        spent = self.display.info_cost_usd
        if spent >= self.max_cost_usd:
            raise PhaseCostLimitExceeded(
                f"{self.display.name} phase spent ${spent:.2f} >= "
                f"${self.max_cost_usd:.2f} limit after {self.display.done} "
                f"items; aborting before starting more paid work"
            )

    def _spend_is_frozen(self) -> bool:
        """True when workers must not start new paid work: the write path
        is dead (results would have nowhere to land) or the phase is over
        its cost ceiling (the producer is about to raise)."""
        if self._writer is not None and self._writer.is_fatal:
            return True
        return (
            self.max_cost_usd is not None
            and self.display.info_cost_usd >= self.max_cost_usd
        )

    def _safe_process(self, item: T, dispatched: set[Hashable],
                      failures: dict[Hashable, int]) -> None:
        if self._shutdown.is_set() or self._spend_is_frozen():
            # Skip silently: not an error, not a retry. The producer
            # raises the real failure (or the operator asked for
            # shutdown); paying for this item first would be the
            # incident's 1,167-discarded-LLM-calls mode again.
            return
        self.display.active_workers += 1
        try:
            self.process_item(item)
        except Exception as e:
            if self._writer is not None and self._writer.is_fatal:
                # The failure is downstream of the dead write path
                # (submit_write refusing the result). Don't count it or
                # un-dispatch for retry — a retry would re-pay for the
                # LLM call and be refused again.
                return
            key = self.item_key(item)
            label = self.item_label(item)
            count = failures.get(key, 0) + 1
            failures[key] = count
            detail = f"{type(e).__name__}: {e}"
            if e.__cause__:
                detail += f" (caused by {type(e.__cause__).__name__}: {e.__cause__})"
            if count >= self.MAX_RETRIES:
                log.error("Giving up on %s after %d failures: %s",
                          label, count, detail)
            else:
                log.warning("Error in %s [%s] (attempt %d/%d): %s",
                            type(self).__name__, label, count, self.MAX_RETRIES, detail)
                self.display.record_error()
                # Un-dispatch so the producer re-polls and retries this item
                dispatched.discard(key)
        finally:
            self.display.active_workers -= 1
