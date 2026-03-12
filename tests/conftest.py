"""Shared test fixtures for PostgreSQL-backed tests."""

import psycopg
import pytest
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from jobbuddy.store import JobStore

TEST_CONNINFO = "service=job-search-buddy-remote"


@pytest.fixture(scope="session", autouse=True)
def ensure_pg_schema():
    """Create schema once per test session."""
    store = JobStore(TEST_CONNINFO)
    store.close()


@pytest.fixture
def store():
    """Per-test JobStore with cleanup isolation.

    Uses autocommit=True (matching production behavior) and
    cleans up all data after the test.
    """
    # Clean before test to handle any residual data
    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.close()

    s = JobStore(TEST_CONNINFO)
    yield s
    s.close()

    # Clean after test
    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.close()


@pytest.fixture
def pg_conninfo():
    """Return conninfo and clean up data after test.

    For integration tests (sync) that create their own connections.
    """
    # Clean before test
    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.close()

    yield TEST_CONNINFO

    # Clean after test
    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.close()
