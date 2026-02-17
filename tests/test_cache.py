"""Tests for ats.cache — SQLite job listing cache."""

import sqlite3
from datetime import datetime, timezone

import pytest

from jobbuddy.cache import (
    get_connection,
    get_jobs_needing_descriptions,
    get_sync_status,
    is_stale,
    job_count,
    query_jobs,
    record_sync_error,
    update_job_descriptions,
    upsert_company_jobs,
)
from jobbuddy.models import Job


def _make_job(id: str = "123", title: str = "PM", location: str = "Seattle", **kw) -> Job:
    return Job(
        id=id,
        title=title,
        location=location,
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        **kw,
    )


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema initialized."""
    c = get_connection(":memory:")
    yield c
    c.close()


class TestUpsertAndQuery:
    def test_upsert_inserts_jobs(self, conn):
        jobs = [_make_job("1", "PM", "Seattle"), _make_job("2", "SWE", "Remote")]
        upsert_company_jobs(conn, "acme", jobs)
        assert job_count(conn) == 2

    def test_upsert_replaces_on_resync(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1", "PM", "Seattle")])
        upsert_company_jobs(conn, "acme", [_make_job("1", "PM Updated", "NYC")])
        rows = query_jobs(conn, company_slug="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM Updated"
        assert rows[0]["location"] == "NYC"

    def test_upsert_marks_disappeared_jobs(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        assert job_count(conn) == 2
        # Re-sync with only job 1 — job 2 should be marked disappeared
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        assert job_count(conn) == 1  # only active jobs
        assert job_count(conn, include_disappeared=True) == 2  # still in DB
        # Verify disappeared_at is set on job 2
        row = conn.execute(
            "SELECT disappeared_at FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["disappeared_at"] is not None

    def test_disappeared_job_reappears(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        # Job 2 disappears
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        assert job_count(conn) == 1
        # Job 2 comes back
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        assert job_count(conn) == 2
        row = conn.execute(
            "SELECT disappeared_at FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["disappeared_at"] is None

    def test_upsert_deduplicates_input(self, conn):
        """Duplicate job IDs in a single fetch don't crash."""
        dupes = [_make_job("1", "PM v1"), _make_job("1", "PM v2")]
        upsert_company_jobs(conn, "acme", dupes)
        rows = query_jobs(conn, company_slug="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM v2"  # last wins

    def test_upsert_isolates_companies(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        upsert_company_jobs(conn, "beta", [_make_job("2"), _make_job("3")])
        # Re-sync acme with empty list should not affect beta
        upsert_company_jobs(conn, "acme", [])
        assert job_count(conn) == 2  # beta's jobs remain (acme's 1 is disappeared)

    def test_upsert_updates_sync_status(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        statuses = get_sync_status(conn, "acme")
        assert len(statuses) == 1
        assert statuses[0]["job_count"] == 2
        assert statuses[0]["error"] is None


class TestQueryFilters:
    @pytest.fixture(autouse=True)
    def populate(self, conn):
        jobs = [
            _make_job("1", "Product Manager", "Seattle, WA", salary="$150k"),
            _make_job("2", "Software Engineer", "Remote", team="Platform"),
            _make_job("3", "Senior PM", "New York, NY"),
            _make_job("4", "Data Scientist", "Seattle, WA"),
        ]
        upsert_company_jobs(conn, "acme", jobs)
        upsert_company_jobs(conn, "beta", [
            _make_job("5", "Product Manager", "London"),
            _make_job("6", "Designer", "Remote"),
        ])

    def test_query_all(self, conn):
        rows = query_jobs(conn, limit=100)
        assert len(rows) == 6

    def test_query_by_company(self, conn):
        rows = query_jobs(conn, company_slug="acme")
        assert len(rows) == 4

    def test_query_title_filter(self, conn):
        rows = query_jobs(conn, title_filter="product manager")
        assert len(rows) == 2
        assert all("Product Manager" in r["title"] for r in rows)

    def test_query_title_filter_comma_or(self, conn):
        rows = query_jobs(conn, title_filter="product manager,data scientist")
        assert len(rows) == 3

    def test_query_location_filter(self, conn):
        rows = query_jobs(conn, location_filter="seattle")
        assert len(rows) == 2

    def test_query_location_filter_comma_or(self, conn):
        rows = query_jobs(conn, location_filter="seattle,remote")
        assert len(rows) == 4

    def test_query_combined_filters(self, conn):
        rows = query_jobs(conn, company_slug="acme", title_filter="engineer", location_filter="remote")
        assert len(rows) == 1
        assert rows[0]["title"] == "Software Engineer"

    def test_query_limit(self, conn):
        rows = query_jobs(conn, limit=2)
        assert len(rows) == 2

    def test_query_includes_last_sync(self, conn):
        rows = query_jobs(conn, company_slug="acme", limit=1)
        assert rows[0]["last_sync"] is not None

    def test_query_excludes_disappeared_by_default(self, conn):
        # Make job 1 disappear by re-syncing without it
        upsert_company_jobs(conn, "acme", [
            _make_job("2", "Software Engineer", "Remote"),
            _make_job("3", "Senior PM", "New York, NY"),
            _make_job("4", "Data Scientist", "Seattle, WA"),
        ])
        rows = query_jobs(conn, company_slug="acme")
        assert len(rows) == 3
        assert all(r["job_id"] != "1" for r in rows)

    def test_query_includes_disappeared_when_asked(self, conn):
        upsert_company_jobs(conn, "acme", [
            _make_job("2", "Software Engineer", "Remote"),
            _make_job("3", "Senior PM", "New York, NY"),
            _make_job("4", "Data Scientist", "Seattle, WA"),
        ])
        rows = query_jobs(conn, company_slug="acme", include_disappeared=True)
        assert len(rows) == 4  # includes disappeared job 1


class TestSyncStatus:
    def test_record_error(self, conn):
        record_sync_error(conn, "broken-co", "Connection timeout")
        statuses = get_sync_status(conn, "broken-co")
        assert len(statuses) == 1
        assert statuses[0]["error"] == "Connection timeout"
        assert statuses[0]["job_count"] == 0

    def test_error_preserves_job_count(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        record_sync_error(conn, "acme", "Rate limited")
        statuses = get_sync_status(conn, "acme")
        assert statuses[0]["job_count"] == 2
        assert statuses[0]["error"] == "Rate limited"

    def test_get_all_sync_status(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        upsert_company_jobs(conn, "beta", [_make_job("2")])
        statuses = get_sync_status(conn)
        assert len(statuses) == 2


class TestStaleness:
    def test_never_synced_is_stale(self, conn):
        assert is_stale(conn, "unknown-co", 24) is True

    def test_recently_synced_not_stale(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        assert is_stale(conn, "acme", 24) is False

    def test_old_sync_is_stale(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        # Manually set last_sync to 48 hours ago
        old_time = "2020-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE sync_status SET last_sync = ? WHERE company_slug = ?",
            (old_time, "acme"),
        )
        conn.commit()
        assert is_stale(conn, "acme", 24) is True


class TestDateValidation:
    def test_valid_published_at(self, conn):
        job = _make_job("1", published_at="2026-02-15")
        upsert_company_jobs(conn, "acme", [job])
        rows = query_jobs(conn, company_slug="acme")
        assert rows[0]["published_at"] == "2026-02-15"

    def test_invalid_published_at_becomes_null(self, conn):
        job = _make_job("1", published_at="not-a-date")
        upsert_company_jobs(conn, "acme", [job])
        rows = query_jobs(conn, company_slug="acme")
        assert rows[0]["published_at"] is None

    def test_none_published_at(self, conn):
        job = _make_job("1", published_at=None)
        upsert_company_jobs(conn, "acme", [job])
        rows = query_jobs(conn, company_slug="acme")
        assert rows[0]["published_at"] is None


class TestDescriptionStorage:
    def test_description_stored_on_upsert(self, conn):
        job = _make_job("1", description="Build cool things for cool people.")
        upsert_company_jobs(conn, "acme", [job])
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "Build cool things for cool people."

    def test_null_description_when_absent(self, conn):
        job = _make_job("1")  # no description
        upsert_company_jobs(conn, "acme", [job])
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] is None

    def test_description_updated_on_resync(self, conn):
        upsert_company_jobs(conn, "acme", [_make_job("1", description="v1")])
        upsert_company_jobs(conn, "acme", [_make_job("1", description="v2")])
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "v2"

    def test_null_description_preserves_existing(self, conn):
        """Re-syncing with NULL description keeps previously-enriched description."""
        upsert_company_jobs(conn, "acme", [_make_job("1", description="enriched text")])
        # Re-sync without description (stub fetcher behavior)
        upsert_company_jobs(conn, "acme", [_make_job("1")])  # no description
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "enriched text"

    def test_nonempty_description_overwrites_existing(self, conn):
        """Re-syncing with a non-NULL description replaces the old one."""
        upsert_company_jobs(conn, "acme", [_make_job("1", description="old")])
        upsert_company_jobs(conn, "acme", [_make_job("1", description="new")])
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "new"

    def test_enrichment_then_resync_preserves(self, conn):
        """Manually setting description on a NULL-description job, then re-syncing preserves it."""
        upsert_company_jobs(conn, "acme", [_make_job("1")])  # no description
        # Simulate enrichment
        conn.execute("UPDATE jobs SET description = 'enriched' WHERE job_id = '1'")
        conn.commit()
        # Re-sync with no description again
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "enriched"


class TestEnrichmentQueries:
    def test_get_jobs_needing_descriptions(self, conn):
        """Returns only active jobs with NULL description for the specified company."""
        upsert_company_jobs(conn, "acme", [
            _make_job("1", description="has one"),
            _make_job("2"),  # needs description
            _make_job("3"),  # needs description
        ])
        upsert_company_jobs(conn, "beta", [_make_job("4")])  # different company

        needing = get_jobs_needing_descriptions(conn, "acme")
        ids = {j["job_id"] for j in needing}
        assert ids == {"2", "3"}

    def test_get_jobs_excludes_disappeared(self, conn):
        """Disappeared jobs don't need descriptions."""
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2")])
        # Make job 2 disappear
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        needing = get_jobs_needing_descriptions(conn, "acme")
        ids = {j["job_id"] for j in needing}
        assert ids == {"1"}

    def test_update_job_descriptions(self, conn):
        """Batch update sets descriptions correctly."""
        upsert_company_jobs(conn, "acme", [_make_job("1"), _make_job("2"), _make_job("3")])
        update_job_descriptions(conn, "acme", {"1": "desc for 1", "2": "desc for 2"})

        row1 = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        row2 = conn.execute("SELECT description FROM jobs WHERE job_id = '2'").fetchone()
        row3 = conn.execute("SELECT description FROM jobs WHERE job_id = '3'").fetchone()
        assert row1["description"] == "desc for 1"
        assert row2["description"] == "desc for 2"
        assert row3["description"] is None

    def test_update_empty_dict_is_noop(self, conn):
        """Updating with empty dict doesn't error."""
        upsert_company_jobs(conn, "acme", [_make_job("1")])
        update_job_descriptions(conn, "acme", {})
        row = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] is None


class TestVectorTables:
    def test_vec_jobs_table_exists(self, conn):
        """sqlite-vec virtual table is created on connection."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
            ).fetchall()
        }
        assert "vec_jobs" in tables

    def test_job_embeddings_table_exists(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "job_embeddings" in tables


class TestEmbeddingGeneration:
    def test_generate_embeddings_creates_entries(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings
        from jobbuddy.embeddings import DIMENSIONS

        job = _make_job("1", description="Build AI products.")
        upsert_company_jobs(conn, "acme", [job])

        fake_vec = [0.1] * DIMENSIONS
        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter([fake_vec])
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            count = generate_embeddings(conn, company_slug="acme")

        assert count == 1
        # Mapping table has an entry
        row = conn.execute(
            "SELECT * FROM job_embeddings WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row is not None

    def test_generate_embeddings_skips_no_description(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings

        job = _make_job("1")  # no description
        upsert_company_jobs(conn, "acme", [job])

        with patch("jobbuddy.cache.embeddings") as mock_emb:
            count = generate_embeddings(conn, company_slug="acme")

        assert count == 0
        mock_emb.embed_texts_iter.assert_not_called()

    def test_generate_embeddings_idempotent(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings
        from jobbuddy.embeddings import DIMENSIONS

        job = _make_job("1", description="Build AI products.")
        upsert_company_jobs(conn, "acme", [job])

        fake_vec = [0.1] * DIMENSIONS
        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter([fake_vec])
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            count1 = generate_embeddings(conn, company_slug="acme")
            count2 = generate_embeddings(conn, company_slug="acme")

        assert count1 == 1
        assert count2 == 0  # already embedded

    def test_generate_embeddings_recomputes_on_description_change(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings
        from jobbuddy.embeddings import DIMENSIONS

        # Insert job with description A and embed it
        job_v1 = _make_job("1", description="Build AI products.")
        upsert_company_jobs(conn, "acme", [job_v1])

        fake_vec_1 = [0.1] * DIMENSIONS
        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter([fake_vec_1])
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            count1 = generate_embeddings(conn, company_slug="acme")
        assert count1 == 1

        hash_v1 = conn.execute(
            "SELECT text_hash FROM job_embeddings WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()["text_hash"]
        assert hash_v1 is not None

        # Re-upsert with changed description
        job_v2 = _make_job("1", description="Lead product strategy for ML platform.")
        upsert_company_jobs(conn, "acme", [job_v2])

        fake_vec_2 = [0.2] * DIMENSIONS
        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter([fake_vec_2])
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            count2 = generate_embeddings(conn, company_slug="acme")
        assert count2 == 1  # recomputed because description changed

        hash_v2 = conn.execute(
            "SELECT text_hash FROM job_embeddings WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()["text_hash"]
        assert hash_v2 is not None
        assert hash_v2 != hash_v1  # hash updated


class TestSemanticSearch:
    def test_semantic_search_returns_results(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings, semantic_search
        from jobbuddy.embeddings import DIMENSIONS

        jobs = [
            _make_job("1", "AI Product Manager", "Seattle", description="Lead AI product strategy."),
            _make_job("2", "Software Engineer", "Remote", description="Build backend services."),
        ]
        upsert_company_jobs(conn, "acme", jobs)

        fake_vecs = [[float(i)] * DIMENSIONS for i in range(2)]
        query_vec = [0.5] * DIMENSIONS

        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter(fake_vecs)
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            generate_embeddings(conn, company_slug="acme")

        from jobbuddy.embeddings import serialize_f32

        results = semantic_search(conn, serialize_f32(query_vec), limit=2)
        assert len(results) == 2
        # Results should have job metadata
        assert all("title" in r for r in results)
        assert all("distance" in r for r in results)

    def test_semantic_search_excludes_disappeared(self, conn):
        from unittest.mock import patch

        from jobbuddy.cache import generate_embeddings, semantic_search
        from jobbuddy.embeddings import DIMENSIONS, serialize_f32

        job = _make_job("1", description="Build things.")
        upsert_company_jobs(conn, "acme", [job])

        fake_vec = [0.1] * DIMENSIONS
        with patch("jobbuddy.cache.embeddings") as mock_emb:
            mock_emb.embed_texts_iter.return_value = iter([fake_vec])
            mock_emb.serialize_f32.side_effect = lambda v: __import__("struct").pack(f"<{len(v)}f", *v)
            generate_embeddings(conn, company_slug="acme")

        # Now make job disappear
        upsert_company_jobs(conn, "acme", [])

        query_vec = serialize_f32([0.1] * DIMENSIONS)
        results = semantic_search(conn, query_vec, limit=10)
        assert len(results) == 0
