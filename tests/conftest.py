"""Shared test fixtures and helpers for PostgreSQL-backed tests.

All DB-touching tests use TEST_CONNINFO. The block_prod_db fixture makes it
categorically impossible for tests to connect to the production database.
"""

import psycopg
import pytest

from jobbuddy.models import Company, Job
from jobbuddy.settings import Settings
from jobbuddy.store import JobStore

TEST_CONNINFO = "service=job-search-buddy-test"

# Standard test companies — every test that touches jobs/sync_status gets these
TEST_COMPANIES = [
    Company(slug="acme", name="Acme Corp", ats="greenhouse", board="acme"),
    Company(slug="beta", name="Beta Inc", ats="greenhouse", board="beta"),
    Company(slug="good", name="Good Co", ats="greenhouse", board="good"),
    Company(slug="bad", name="Bad Co", ats="greenhouse", board="bad"),
    Company(slug="broken-co", name="Broken Co"),
    Company(slug="workday-co", name="Workday Co", ats="workday", board="workday-co"),
]


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
    settings_mod._settings = Settings(
        pg_service="job-search-buddy-test",
        research_endpoint="https://test.openai.azure.com/",
    )

    original_connect = psycopg.connect

    _PROD_SERVICES = {"job-search-buddy-remote", "job-search-buddy-azure"}

    def guarded_connect(conninfo="", **kwargs):
        conn_str = str(conninfo) + str(kwargs)
        if any(svc in conn_str for svc in _PROD_SERVICES):
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
    """Insert jobs into the DB. Used by test_strip and test_sync.

    The company row must already exist (seeded by the test_companies fixture).
    """
    store = JobStore(conninfo)
    store.upsert_jobs(slug, jobs)
    store.close()


def clean_tables(conninfo: str) -> None:
    """Delete all rows from test tables (FK-safe ordering)."""
    conn = psycopg.connect(conninfo, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.execute("DELETE FROM activity_log")
    conn.execute("DELETE FROM companies")
    conn.close()


# ---------------------------------------------------------------------------
# Schema migration (once per session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def ensure_pg_schema():
    """Apply migrations and seed test companies once per session.

    Companies are seeded once and persist across all tests. Per-test cleanup
    only touches child tables (jobs, sync_status, activity_log), avoiding
    ~12 extra DB round-trips per test for company insert/delete.
    """
    from jobbuddy.migrations import apply_migrations

    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    apply_migrations(conn)
    conn.close()

    clean_tables(TEST_CONNINFO)
    s = JobStore(TEST_CONNINFO)
    for company in TEST_COMPANIES:
        s.save_company(company)
    s.close()


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_test_data():
    """Clean child tables between tests. Companies persist from session setup."""
    conn = psycopg.connect(TEST_CONNINFO, autocommit=True)
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sync_status")
    conn.execute("DELETE FROM activity_log")
    conn.execute(
        "UPDATE companies SET short_bio = NULL, long_bio = NULL,"
        " bio_researched_at = NULL, bio_model = NULL"
    )
    conn.close()


@pytest.fixture
def store():
    """Per-test JobStore connected to the test database."""
    s = JobStore(TEST_CONNINFO)
    yield s
    s.close()


@pytest.fixture
def pg_conninfo():
    """Return conninfo for tests that create their own connections."""
    return TEST_CONNINFO
