"""Tests for jobbuddy.store -- JobStore class (PostgreSQL)."""

from datetime import datetime, timezone

import psycopg
import pytest

from jobbuddy.models import Job
from jobbuddy.store import JobStore

from conftest import make_job


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_jobs_table_has_surrogate_key(self, store):
        """jobs table uses SERIAL PRIMARY KEY (id column)."""
        row = store.conn.execute("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'jobs' AND column_name = 'id'
        """).fetchone()
        assert row is not None
        assert row["data_type"] == "integer"

    def test_jobs_has_unique_constraint(self, store):
        """(company_slug, job_id) is UNIQUE."""
        rows = store.conn.execute("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'jobs' AND constraint_type = 'UNIQUE'
        """).fetchall()
        assert len(rows) >= 1

    def test_extract_columns_exist(self, store):
        """short_jd and description_normalized exist on jobs after migration 011."""
        rows = store.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'jobs' AND column_name IN ('short_jd', 'description_normalized')
        """).fetchall()
        cols = {r["column_name"] for r in rows}
        assert cols == {"short_jd", "description_normalized"}

    def test_embedding_tables_dropped(self, store):
        """Migration 011 dropped job_embeddings and query_embeddings."""
        rows = store.conn.execute("""
            SELECT tablename FROM pg_tables
            WHERE tablename IN ('job_embeddings', 'query_embeddings')
        """).fetchall()
        assert rows == []

    def test_context_manager(self, pg_conninfo):
        """JobStore works as a context manager."""
        with JobStore(pg_conninfo) as s:
            s.upsert_jobs("acme", [make_job("1")])
            assert s.job_count() == 1


# ---------------------------------------------------------------------------
# Upsert + Query
# ---------------------------------------------------------------------------


class TestUpsertAndQuery:
    def test_upsert_inserts_jobs(self, store):
        jobs = [make_job("1", "PM", "Seattle"), make_job("2", "SWE", "Remote")]
        store.upsert_jobs("acme", jobs)
        rows = store.query_jobs()
        assert len(rows) == 2

    def test_upsert_replaces_on_resync(self, store):
        store.upsert_jobs("acme", [make_job("1", "PM", "Seattle")])
        store.upsert_jobs("acme", [make_job("1", "PM Updated", "NYC")])
        rows = store.query_jobs(companies=["acme"])
        assert len(rows) == 1
        assert rows[0]["title"] == "PM Updated"
        assert rows[0]["location"] == "NYC"

    def test_upsert_marks_removed_jobs(self, store):
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        assert len(store.query_jobs()) == 2
        store.upsert_jobs("acme", [make_job("1")])
        assert len(store.query_jobs()) == 1
        assert len(store.query_jobs(include_removed=True)) == 2

    def test_removed_job_reappears(self, store):
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.upsert_jobs("acme", [make_job("1")])
        assert len(store.query_jobs()) == 1
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        assert len(store.query_jobs()) == 2

    def test_upsert_deduplicates_input(self, store):
        dupes = [make_job("1", "PM v1"), make_job("1", "PM v2")]
        store.upsert_jobs("acme", dupes)
        rows = store.query_jobs(companies=["acme"])
        assert len(rows) == 1
        assert rows[0]["title"] == "PM v2"

    def test_query_jobs_single_company_filter(self, store):
        """query_jobs(companies=[...]) should not crash on single-company queries."""
        store.upsert_jobs("acme", [make_job("1", "PM")])
        store.upsert_jobs("beta", [make_job("2", "SWE")])
        rows = store.query_jobs(companies=["acme"])
        assert len(rows) == 1
        assert rows[0]["title"] == "PM"

    def test_upsert_isolates_companies(self, store):
        store.upsert_jobs("acme", [make_job("1")])
        store.upsert_jobs("beta", [make_job("2"), make_job("3")])
        store.upsert_jobs("acme", [])
        assert len(store.query_jobs()) == 2

    def test_null_description_preserves_existing(self, store):
        """Re-syncing with NULL description keeps previously-enriched description."""
        store.upsert_jobs("acme", [make_job("1", description="enriched")])
        store.upsert_jobs("acme", [make_job("1")])
        rows = store.query_jobs(companies=["acme"])
        assert rows[0]["description"] == "enriched"

    def test_description_change_nulls_distill_outputs(self, store):
        """Re-upserting with a changed description NULLs short_jd and
        description_normalized so the distill phase reprocesses the row.
        """
        store.upsert_jobs("acme", [make_job("1", description="v1 body")])
        # Simulate the distill phase having populated its outputs.
        store.conn.execute(
            "UPDATE jobs SET short_jd = %s, description_normalized = %s "
            "WHERE company_slug = %s AND job_id = %s",
            ("capsule v1", "normalized v1", "acme", "1"),
        )
        store.upsert_jobs("acme", [make_job("1", description="v2 body")])
        row = store.conn.execute(
            "SELECT description, short_jd, description_normalized FROM jobs "
            "WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["description"] == "v2 body"
        assert row["short_jd"] is None
        assert row["description_normalized"] is None

    def test_unchanged_description_preserves_distill_outputs(self, store):
        """Re-upserting with the same description preserves distill outputs."""
        store.upsert_jobs("acme", [make_job("1", description="stable body")])
        store.conn.execute(
            "UPDATE jobs SET short_jd = %s, description_normalized = %s "
            "WHERE company_slug = %s AND job_id = %s",
            ("capsule", "normalized", "acme", "1"),
        )
        store.upsert_jobs("acme", [make_job("1", description="stable body")])
        row = store.conn.execute(
            "SELECT short_jd, description_normalized FROM jobs "
            "WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["short_jd"] == "capsule"
        assert row["description_normalized"] == "normalized"

    def test_null_incoming_description_preserves_distill_outputs(self, store):
        """Re-upsert with NULL description (stub re-list) doesn't blow away distill."""
        store.upsert_jobs("acme", [make_job("1", description="body")])
        store.conn.execute(
            "UPDATE jobs SET short_jd = %s, description_normalized = %s "
            "WHERE company_slug = %s AND job_id = %s",
            ("capsule", "normalized", "acme", "1"),
        )
        store.upsert_jobs("acme", [make_job("1")])  # description=None
        row = store.conn.execute(
            "SELECT short_jd, description_normalized FROM jobs "
            "WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["short_jd"] == "capsule"
        assert row["description_normalized"] == "normalized"

    def test_surrogate_key_assigned(self, store):
        """Jobs get an integer surrogate key (id)."""
        store.upsert_jobs("acme", [make_job("1")])
        row = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()
        assert row["id"] is not None
        assert isinstance(row["id"], int)

    def test_repost_detection_logs(self, store, caplog):
        """Re-posting a removed job logs an INFO message."""
        import logging
        store.upsert_jobs("acme", [make_job("1", "PM"), make_job("2", "SWE")])
        store.upsert_jobs("acme", [make_job("1", "PM")])  # removes job 2
        assert len(store.query_jobs()) == 1

        with caplog.at_level(logging.INFO, logger="jobbuddy.store"):
            store.upsert_jobs("acme", [make_job("1", "PM"), make_job("2", "SWE")])

        repost_msgs = [r for r in caplog.records if "repost" in r.message.lower()]
        assert len(repost_msgs) == 1
        assert "2" in repost_msgs[0].message

    def test_removed_job_has_removed_at_set(self, store):
        """Removing a job sets removed_at via trigger."""
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.upsert_jobs("acme", [make_job("1")])  # removes job 2
        row = store.conn.execute(
            "SELECT listing_status, removed_at FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["listing_status"] == "removed"
        assert row["removed_at"] is not None

    def test_reappeared_job_clears_removed_at(self, store):
        """Re-appearing job clears removed_at via trigger."""
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.upsert_jobs("acme", [make_job("1")])  # removes job 2
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])  # reappears
        row = store.conn.execute(
            "SELECT listing_status, removed_at FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["listing_status"] == "active"
        assert row["removed_at"] is None


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------


class TestQueryFilters:
    @pytest.fixture(autouse=True)
    def populate(self, store):
        store.upsert_jobs("acme", [
            make_job("1", "Product Manager", "Seattle, WA", salary="$150k"),
            make_job("2", "Software Engineer", "Remote", team="Platform"),
            make_job("3", "Senior PM", "New York, NY"),
            make_job("4", "Data Scientist", "Seattle, WA"),
        ])
        store.upsert_jobs("beta", [
            make_job("5", "Product Manager", "London"),
            make_job("6", "Designer", "Remote"),
        ])

    def test_query_all(self, store):
        rows = store.query_jobs(limit=100)
        assert len(rows) == 6

    def test_query_by_company(self, store):
        rows = store.query_jobs(companies=["acme"])
        assert len(rows) == 4

    def test_query_title_filter(self, store):
        rows = store.query_jobs(title="product manager")
        assert len(rows) == 2

    def test_query_location_filter(self, store):
        rows = store.query_jobs(location="seattle")
        assert len(rows) == 2

    def test_query_combined_filters(self, store):
        rows = store.query_jobs(companies=["acme"], title="engineer", location="remote")
        assert len(rows) == 1
        assert rows[0]["title"] == "Software Engineer"

    def test_query_limit(self, store):
        rows = store.query_jobs(limit=2)
        assert len(rows) == 2


class TestFullTextSearch:
    """Tests for PostgreSQL FTS replacing ILIKE title filter."""

    @pytest.fixture(autouse=True)
    def populate(self, store):
        store.upsert_jobs("acme", [
            make_job("1", "Software Engineer", "Seattle", description="Build backend services in Python."),
            make_job("2", "Software Development Engineer", "Seattle", description="SDE role building distributed systems."),
            make_job("3", "Senior Engineering Manager", "Remote", description="Lead a team of software engineers."),
            make_job("4", "Product Manager", "Seattle", description="Drive product strategy for developer tools."),
            make_job("5", "Data Scientist", "NYC", description="ML models for recommendation engine."),
        ])
        for jid in ["1", "2", "3", "4", "5"]:
            store.conn.execute(
                "UPDATE jobs SET description_normalized = description WHERE job_id = %s", (jid,)
            )

    def test_fts_stemming(self, store):
        """'engineer' matches 'Engineering' and 'Engineers' via stemming."""
        rows = store.query_jobs(title="engineer")
        titles = {r["title"] for r in rows}
        assert "Software Engineer" in titles
        assert "Software Development Engineer" in titles
        assert "Senior Engineering Manager" in titles

    def test_fts_searches_description(self, store):
        """FTS searches description_normalized, not just title."""
        rows = store.query_jobs(title="python")
        assert len(rows) >= 1
        assert any(r["job_id"] == "1" for r in rows)

    def test_fts_multi_word_query(self, store):
        """Multi-word query matches jobs containing all terms."""
        rows = store.query_jobs(title="software engineer")
        titles = {r["title"] for r in rows}
        assert "Software Engineer" in titles
        assert "Software Development Engineer" in titles

    def test_fts_websearch_or_syntax(self, store):
        """websearch_to_tsquery OR syntax works."""
        rows = store.query_jobs(title="engineer OR scientist")
        titles = {r["title"] for r in rows}
        assert "Software Engineer" in titles
        assert "Data Scientist" in titles

    def test_fts_no_match(self, store):
        """FTS returns empty when no terms match."""
        rows = store.query_jobs(title="blockchain")
        assert len(rows) == 0

    def test_location_still_ilike(self, store):
        """Location filter still uses ILIKE substring matching."""
        rows = store.query_jobs(location="seat")
        assert len(rows) >= 1
        assert all("Seattle" in (r["location"] or "") for r in rows)


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


class TestDescriptions:
    def test_get_jobs_needing_descriptions(self, store):
        store.upsert_jobs("acme", [
            make_job("1", description="has one"),
            make_job("2"),
            make_job("3"),
        ])
        needing = store.get_jobs_needing_descriptions("acme")
        ids = {j["job_id"] for j in needing}
        assert ids == {"2", "3"}

    def test_update_descriptions(self, store):
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.update_descriptions("acme", {"1": "desc for 1"})
        row = store.conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        assert row["description"] == "desc for 1"


# ---------------------------------------------------------------------------
# Sync bookkeeping
# ---------------------------------------------------------------------------


class TestSyncBookkeeping:
    def test_is_stale_never_synced(self, store):
        assert store.is_stale("unknown-co", 24) is True

    def test_is_stale_recently_synced(self, store):
        store.upsert_jobs("acme", [make_job("1")])
        assert store.is_stale("acme", 24) is False

    def test_record_sync_error(self, store):
        store.record_sync_error("broken-co", "Connection timeout")
        row = store.conn.execute(
            "SELECT * FROM sync_status WHERE company_slug = %s", ("broken-co",)
        ).fetchone()
        assert row["error"] == "Connection timeout"

