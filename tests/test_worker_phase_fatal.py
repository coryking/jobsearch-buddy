"""Tests for WorkerPhase behavior when the WriteQueue goes fatal, and for
the per-phase cost ceiling.

Background (2026-07-10 incident): a NUL byte in distill output made the
WriteQueue bail, but instead of crashing the sync as documented:

1. run() deadlocked — flush() raised inside the producer loop, skipping the
   worker-sentinel sends, so ThreadPoolExecutor.__exit__ blocked forever on
   workers parked in work_queue.get(). The process hung for 10 days.
2. Workers kept making paid LLM calls whose results had nowhere to land —
   1,167 calls were paid for and discarded against the dead queue, with the
   per-item retry logic re-paying for failures.
3. flush() could miss the fatal flag entirely: the writer called task_done()
   before _bail() set _fatal, so a single flush() raced past the error.

These tests pin the designed behavior: fatal means the phase terminates,
loudly, without starting new paid work. The cost-ceiling tests pin the
second spend bound: a phase refuses to exceed its per-run budget.
"""

from __future__ import annotations

import threading

import pytest

from jobbuddy.store import JobStore
from jobbuddy.sync.base import PhaseCostLimitExceeded, WorkerPhase, WriteQueue
from jobbuddy.sync.display import PhaseState


class StubPhase(WorkerPhase[dict]):
    """Minimal concrete phase over an in-memory work list.

    An item is "done" once its write callable has executed against the DB
    writer — mirroring the NULL-column-presence predicate real phases poll.
    `process_calls` records every process_item invocation: in the real
    distill phase that is the point where money is spent, so assertions
    about it are assertions about spend.
    """

    def __init__(self, conninfo: str, items: list[dict], *, max_workers: int = 1,
                 max_cost_usd: float | None = None,
                 cost_per_item: float = 0.0,
                 poison_write_keys: frozenset[int] = frozenset(),
                 wait_for_fatal_after_poison: bool = False):
        kwargs = {}
        if max_cost_usd is not None:
            kwargs["max_cost_usd"] = max_cost_usd
        super().__init__(
            conninfo, max_workers=max_workers,
            display=PhaseState("Stub"), **kwargs,
        )
        self._items = items
        self._written: set[int] = set()
        self._written_lock = threading.Lock()
        self._cost_per_item = cost_per_item
        self._poison_write_keys = poison_write_keys
        self._wait_for_fatal_after_poison = wait_for_fatal_after_poison
        self.process_calls: list[int] = []

    def count_remaining(self) -> int:
        with self._written_lock:
            return len([i for i in self._items if i["id"] not in self._written])

    def poll_work(self, batch_size: int) -> list[dict]:
        with self._written_lock:
            pending = [i for i in self._items if i["id"] not in self._written]
        return pending[:batch_size]

    def item_key(self, item: dict) -> int:
        return item["id"]

    def process_item(self, item: dict) -> None:
        self.process_calls.append(item["id"])
        if self._cost_per_item:
            self.display.add_cost(self._cost_per_item)
        key = item["id"]
        if key in self._poison_write_keys:
            def poison(store: JobStore) -> None:
                raise ValueError(f"poison write for item {key}")
            self.submit_write(poison)
            if self._wait_for_fatal_after_poison:
                # Deterministic ordering for the spend-guard test: don't
                # return (and let the worker pull the next item) until the
                # writer thread has observably gone fatal.
                assert self._writer is not None
                _wait_for(lambda: self._writer.is_fatal)
            return

        def ok(store: JobStore, key=key) -> None:
            store.conn.execute("SELECT 1").fetchone()
            with self._written_lock:
                self._written.add(key)
        self.submit_write(ok)
        self.display.advance()


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = threading.Event()
    t = threading.Timer(timeout, deadline.set)
    t.daemon = True
    t.start()
    try:
        while not deadline.is_set():
            if predicate():
                return True
            deadline.wait(0.05)
        return predicate()
    finally:
        t.cancel()


def _run_phase_with_timeout(phase: WorkerPhase, timeout: float = 20.0):
    """Run phase.run() on a thread; return (finished, exception)."""
    result: dict = {"exc": None}

    def target() -> None:
        try:
            phase.run()
        except BaseException as e:  # noqa: BLE001 — capture for assertion
            result["exc"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), result["exc"]


ITEMS = [{"id": n} for n in range(1, 6)]


class TestFatalWriterTerminatesPhase:
    def test_fatal_writer_raises_instead_of_hanging(self, pg_conninfo):
        """A poison write must crash run() with the WriteQueue's fatal
        error — not deadlock the executor join forever (the 10-day hang)."""
        phase = StubPhase(
            pg_conninfo, ITEMS, max_workers=2,
            poison_write_keys=frozenset({1, 2, 3, 4, 5}),
        )
        finished, exc = _run_phase_with_timeout(phase)
        assert finished, "run() hung after WriteQueue went fatal (deadlock)"
        assert isinstance(exc, RuntimeError)
        assert "poison write" in str(exc)

    def test_flush_sees_fatal_set_by_same_item(self, pg_conninfo):
        """The writer must publish _fatal BEFORE task_done, so the very
        first flush() after a poison write raises rather than racing past."""
        def poison(store: JobStore) -> None:
            raise ValueError("data bug, not transport")

        wq = WriteQueue(conninfo_factory=lambda: pg_conninfo)
        wq.start()
        try:
            wq.submit(poison)
            with pytest.raises(RuntimeError, match="data bug"):
                wq.flush()
        finally:
            wq.stop()


class TestNoSpendAfterFatal:
    def test_workers_stop_processing_once_writer_is_fatal(self, pg_conninfo):
        """process_item is where money is spent. Once the write path is
        dead, no further items may be processed — pay-then-discard is the
        1,167-wasted-calls failure mode."""
        phase = StubPhase(
            pg_conninfo, ITEMS, max_workers=1,
            poison_write_keys=frozenset({1}),
            wait_for_fatal_after_poison=True,
        )
        finished, exc = _run_phase_with_timeout(phase)
        assert finished, "run() hung after WriteQueue went fatal"
        assert isinstance(exc, RuntimeError)
        # Item 1 poisoned the queue and blocked until fatal was visible;
        # with one worker, items 2-5 were only ever pulled after that, and
        # every one of them must have been skipped, unpaid.
        assert phase.process_calls == [1], (
            f"paid work continued after writer went fatal: {phase.process_calls}"
        )

    def test_skipped_items_are_not_counted_as_retryable_errors(self, pg_conninfo):
        """Post-fatal skips must not churn the retry path (un-dispatch +
        re-poll + re-pay) or inflate the error counter the way the
        incident's errors=1167 did."""
        phase = StubPhase(
            pg_conninfo, ITEMS, max_workers=1,
            poison_write_keys=frozenset({1}),
            wait_for_fatal_after_poison=True,
        )
        _run_phase_with_timeout(phase)
        assert phase.display.errors == 0, (
            "post-fatal skips were recorded as per-item errors (retry churn)"
        )


class TestPhaseCostCeiling:
    def test_phase_aborts_when_cost_limit_reached(self, pg_conninfo):
        """With a $15 limit and $10/item, a single worker must stop after
        two items: the third check sees $20 >= $15 and the phase dies
        loudly instead of spending unbounded."""
        phase = StubPhase(
            pg_conninfo, [{"id": n} for n in range(1, 4)],
            max_workers=1, max_cost_usd=15.0, cost_per_item=10.0,
        )
        finished, exc = _run_phase_with_timeout(phase)
        assert finished, "run() hung instead of enforcing the cost limit"
        assert isinstance(exc, PhaseCostLimitExceeded)
        assert len(phase.process_calls) <= 2, (
            f"phase kept paying past its cost limit: {phase.process_calls}"
        )

    def test_no_limit_processes_everything(self, pg_conninfo):
        """max_cost_usd=None (the default) preserves existing behavior."""
        phase = StubPhase(
            pg_conninfo, ITEMS, max_workers=2, cost_per_item=10.0,
        )
        finished, exc = _run_phase_with_timeout(phase)
        assert finished
        assert exc is None
        assert sorted(phase.process_calls) == [1, 2, 3, 4, 5]
