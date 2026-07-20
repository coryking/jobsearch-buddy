"""Tests for WriteQueue reconnect-on-closed-connection behavior.

Background: WriteQueue holds a single long-lived psycopg connection. When
that connection dies (idle timeout, server reset, Azure Entra token
expiry around the 1h mark), every subsequent write previously raised
"the connection is closed" and was silently dropped. These tests cover
the reconnect-and-retry behavior that fixes that.
"""

from __future__ import annotations

import logging
import threading

import pytest

from jobbuddy.store import JobStore
from jobbuddy.sync.base import WriteQueue


@pytest.fixture
def conninfo_factory(pg_conninfo):
    """Wraps the test conninfo as a factory and counts invocations."""

    calls = {"n": 0}

    def factory() -> str:
        calls["n"] += 1
        return pg_conninfo

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


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


class TestWriteQueueReconnect:
    def test_closed_connection_triggers_reconnect_and_write_lands(
        self, conninfo_factory, pg_conninfo
    ):
        """When the writer's connection dies mid-run, the next submitted
        callable must transparently reconnect and the write must land."""
        captured_stores: list[JobStore] = []

        # First write: capture the store, then close its connection so the
        # NEXT submission lands on a dead connection.
        def first_write(store: JobStore) -> None:
            captured_stores.append(store)

        # Second write: actually does a write. Must succeed despite the
        # connection from the first call being closed.
        def second_write(store: JobStore) -> None:
            captured_stores.append(store)
            store.conn.execute(
                "INSERT INTO sync_status (company_slug, last_sync, job_count)"
                " VALUES (%s, NOW(), %s)"
                " ON CONFLICT (company_slug) DO UPDATE SET job_count = EXCLUDED.job_count",
                ("acme", 42),
            )

        wq = WriteQueue(conninfo_factory=conninfo_factory)
        wq.start()
        try:
            wq.submit(first_write)
            wq.flush()
            # Murder the connection out from under the writer thread.
            assert captured_stores, "first write must have run"
            captured_stores[0].conn.close()

            wq.submit(second_write)
            wq.flush()
        finally:
            wq.stop()

        # Verify the second write actually landed.
        store = JobStore(pg_conninfo)
        try:
            row = store.conn.execute(
                "SELECT job_count FROM sync_status WHERE company_slug = %s",
                ("acme",),
            ).fetchone()
        finally:
            store.close()

        assert row is not None, "second write was silently dropped"
        assert row["job_count"] == 42

    def test_factory_is_reinvoked_on_reconnect(self, conninfo_factory):
        """Reconnect must call the factory again (so a fresh Entra token
        would be picked up)."""
        captured_stores: list[JobStore] = []

        def first_write(store: JobStore) -> None:
            captured_stores.append(store)

        def second_write(store: JobStore) -> None:
            captured_stores.append(store)
            store.conn.execute("SELECT 1").fetchone()

        wq = WriteQueue(conninfo_factory=conninfo_factory)
        wq.start()
        try:
            wq.submit(first_write)
            wq.flush()
            calls_before = conninfo_factory.calls["n"]
            assert calls_before == 1

            captured_stores[0].conn.close()

            wq.submit(second_write)
            wq.flush()
        finally:
            wq.stop()

        assert conninfo_factory.calls["n"] > calls_before, (
            "factory must be re-invoked on reconnect to refresh Entra token"
        )

    def test_non_connection_exception_is_fatal_not_retried(
        self, conninfo_factory, caplog
    ):
        """A callable that raises a non-connection error (e.g. ValueError)
        must NOT trigger a reconnect — it marks the queue fatal, and the
        next flush() re-raises so the phase crashes loudly (the row was
        paid for upstream; dropping it silently burns money)."""

        def bad_write(store: JobStore) -> None:
            raise ValueError("data bug, not transport")

        wq = WriteQueue(conninfo_factory=conninfo_factory)
        wq.start()
        try:
            with caplog.at_level(logging.WARNING, logger="jobbuddy.sync.base"):
                wq.submit(bad_write)
                with pytest.raises(RuntimeError, match="data bug"):
                    wq.flush()
        finally:
            wq.stop()

        assert wq.is_fatal
        assert conninfo_factory.calls["n"] == 1, (
            "factory must NOT be re-invoked for non-connection errors"
        )
        assert any("data bug" in rec.message or "ValueError" in rec.message
                   for rec in caplog.records), \
            f"expected error log; got {[r.message for r in caplog.records]}"
