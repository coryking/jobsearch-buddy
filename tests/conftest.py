"""Shared test fixtures and helpers for PostgreSQL-backed tests.

All DB-touching tests use TEST_CONNINFO. The block_prod_db fixture makes it
categorically impossible for tests to connect to the production database.
"""

import psycopg
import pytest
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from jobbuddy.models import Job
from jobbuddy.settings import Settings
from jobbuddy.store import JobStore

TEST_CONNINFO = "service=job-search-buddy-test"


# ---------------------------------------------------------------------------
# Safety: block production DB connections for the entire test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def block_prod_db():
    """Categorically prevent tests from connecting to production.

    Layer 1: Override settings singleton so bare JobStore() resolves to test DB.
    Layer 2: Wrap psycopg.connect to reject any conninfo containing the prod
    service name — catches hardcoded conninfo that bypasses settings.
    """
    import jobbuddy.settings as settings_mod
    settings_mod._settings = Settings(pg_service="job-search-buddy-test")

    original_connect = psycopg.connect

    def guarded_connect(conninfo="", **kwargs):
        conn_str = str(conninfo) + str(kwargs)
        if "job-search-buddy-remote" in conn_str:
            raise RuntimeError(
                f"Test tried to connect to PRODUCTION database! conninfo={conninfo}"
            )
        return original_connect(conninfo, **kwargs)

    psycopg.connect = guarded_connect
    yield
    psycopg.connect = original_connect
    settings_mod._settings = None


# ---------------------------------------------------------------------------
# Shared helpers (not fixtures — plain functions importable by test modules)
# ---------------------------------------------------------------------------


def make_job(
    id: str = "123",
    title: str = "PM",
    location: str = "Seattle",
    ats_metadata: dict | None = None,
    **kw,
) -> Job:
    """Build a Job for testing. Single definition used by all test modules."""
    return Job(
        id=id,
        title=title,
        location=location,
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        ats_metadata=ats_metadata,
        **kw,
    )


def seed_jobs(conninfo: str, slug: str, jobs: list[Job]) -> None:
    """Insert jobs into the DB. Used by test_strip and test_sync."""
    store = JobStore(conninfo)
    store.upsert_jobs(slug, jobs)
    store.close()


def clean_tables(conninfo: str) -> None:
    """Delete all rows from test tables."""
    conn = psycopg.connect(conninfo, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.execute("DELETE FROM activity_log")
    conn.close()


# ---------------------------------------------------------------------------
# Schema migration (once per session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def ensure_pg_schema():
    """Apply migrations once per test session."""
    from jobbuddy.migrations import apply_migrations

    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    apply_migrations(conn)
    conn.close()


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    """Per-test JobStore with cleanup isolation.

    Uses autocommit=True (matching production behavior) and
    cleans up all data before and after the test.
    """
    clean_tables(TEST_CONNINFO)
    s = JobStore(TEST_CONNINFO)
    yield s
    s.close()
    clean_tables(TEST_CONNINFO)


@pytest.fixture
def pg_conninfo():
    """Return conninfo and clean up data before/after test.

    For integration tests (sync) that create their own connections.
    """
    clean_tables(TEST_CONNINFO)
    yield TEST_CONNINFO
    clean_tables(TEST_CONNINFO)
