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

    def test_embedding_column_exists(self, store):
        """jobs table has a vector(1536) embedding column."""
        row = store.conn.execute("""
            SELECT column_name, udt_name FROM information_schema.columns
            WHERE table_name = 'jobs' AND column_name = 'embedding'
        """).fetchone()
        assert row is not None
        assert row["udt_name"] == "vector"

    def test_context_manager(self, pg_conninfo):
        """JobStore works as a context manager."""
        with JobStore(pg_conninfo) as s:
            s.upsert_jobs("acme", [make_job("1")])
            assert s.job_count() == 1
        # clean up
        conn = psycopg.connect(pg_conninfo)
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM sync_status")
        conn.commit()
        conn.close()


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
        rows = store.query_jobs(company="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM Updated"
        assert rows[0]["location"] == "NYC"

    def test_upsert_marks_disappeared_jobs(self, store):
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        assert len(store.query_jobs()) == 2
        store.upsert_jobs("acme", [make_job("1")])
        assert len(store.query_jobs()) == 1
        assert len(store.query_jobs(include_disappeared=True)) == 2

    def test_disappeared_job_reappears(self, store):
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.upsert_jobs("acme", [make_job("1")])
        assert len(store.query_jobs()) == 1
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        assert len(store.query_jobs()) == 2

    def test_upsert_deduplicates_input(self, store):
        dupes = [make_job("1", "PM v1"), make_job("1", "PM v2")]
        store.upsert_jobs("acme", dupes)
        rows = store.query_jobs(company="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM v2"

    def test_upsert_isolates_companies(self, store):
        store.upsert_jobs("acme", [make_job("1")])
        store.upsert_jobs("beta", [make_job("2"), make_job("3")])
        store.upsert_jobs("acme", [])
        assert len(store.query_jobs()) == 2

    def test_null_description_preserves_existing(self, store):
        """Re-syncing with NULL description keeps previously-enriched description."""
        store.upsert_jobs("acme", [make_job("1", description="enriched")])
        store.upsert_jobs("acme", [make_job("1")])
        rows = store.query_jobs(company="acme")
        assert rows[0]["description"] == "enriched"

    def test_surrogate_key_assigned(self, store):
        """Jobs get an integer surrogate key (id)."""
        store.upsert_jobs("acme", [make_job("1")])
        row = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()
        assert row["id"] is not None
        assert isinstance(row["id"], int)


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
        rows = store.query_jobs(company="acme")
        assert len(rows) == 4

    def test_query_title_filter(self, store):
        rows = store.query_jobs(title="product manager")
        assert len(rows) == 2

    def test_query_title_filter_comma_or(self, store):
        rows = store.query_jobs(title="product manager,data scientist")
        assert len(rows) == 3

    def test_query_location_filter(self, store):
        rows = store.query_jobs(location="seattle")
        assert len(rows) == 2

    def test_query_combined_filters(self, store):
        rows = store.query_jobs(company="acme", title="engineer", location="remote")
        assert len(rows) == 1
        assert rows[0]["title"] == "Software Engineer"

    def test_query_limit(self, store):
        rows = store.query_jobs(limit=2)
        assert len(rows) == 2


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


# ---------------------------------------------------------------------------
# Embeddings (pgvector)
# ---------------------------------------------------------------------------


class TestEmbeddings:

    def _insert_jobs_with_stripped(self, store, *stripped_descs):
        """Insert jobs with description_stripped set."""
        jobs = [
            make_job(str(i + 1), description=f"full desc {i}", description_stripped=desc)
            for i, desc in enumerate(stripped_descs)
        ]
        store.upsert_jobs("acme", jobs)
        for i, desc in enumerate(stripped_descs):
            store.conn.execute(
                "UPDATE jobs SET description_stripped = %s WHERE job_id = %s",
                (desc, str(i + 1)),
            )
        ids = []
        for i in range(len(stripped_descs)):
            row = store.conn.execute(
                "SELECT id FROM jobs WHERE job_id = %s", (str(i + 1),)
            ).fetchone()
            ids.append(row["id"])
        return ids

    def _make_embedding(self, dims=1536, val=0.1):
        """Create an embedding as a list of floats."""
        return [val] * dims

    def test_jobs_needing_embeddings_count(self, store):
        """Jobs with description_stripped need embeddings; those without don't."""
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        store.upsert_jobs("acme", [
            make_job("1", description="Build AI."),
            make_job("2", description="Lead teams."),
            make_job("3"),
        ])
        store.conn.execute("UPDATE jobs SET description_stripped = 'Build AI.' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET description_stripped = 'Lead teams.' WHERE job_id = '2'")
        count = store.count_jobs_needing_embeddings()
        assert count == 2

    def test_jobs_needing_embeddings_list(self, store):
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        jobs = store.list_jobs_needing_embeddings()
        assert len(jobs) == 2
        assert all("id" in j and "job_id" in j for j in jobs)

    def test_count_and_list_consistency(self, store):
        """Count and list modes return consistent results."""
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        count = store.count_jobs_needing_embeddings()
        jobs = store.list_jobs_needing_embeddings()
        assert count == len(jobs)

    def test_store_embedding(self, store):
        """store_embedding sets the embedding column."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        vec = self._make_embedding()

        store.store_embedding(job_id, vec)

        row = store.conn.execute(
            "SELECT embedding FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["embedding"] is not None

    def test_store_embedding_makes_job_not_needing(self, store):
        """After storing an embedding, the job no longer needs one."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        assert store.count_jobs_needing_embeddings() == 1

        vec = self._make_embedding()
        store.store_embedding(job_id, vec)
        assert store.count_jobs_needing_embeddings() == 0

    def test_update_stripped_description_deletes_embedding(self, store):
        """Updating stripped description cascades: clears the stale embedding."""
        ids = self._insert_jobs_with_stripped(store, "version 1")
        job_id = ids[0]
        vec = self._make_embedding()

        store.store_embedding(job_id, vec)
        assert store.count_jobs_needing_embeddings() == 0

        store.update_stripped_description(job_id, "version 2")
        assert store.count_jobs_needing_embeddings() == 1

        row = store.conn.execute(
            "SELECT embedding FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["embedding"] is None

    def test_clear_stripped_descriptions_deletes_embeddings(self, store):
        """Clearing all stripped descriptions also clears all embeddings."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        vec = self._make_embedding()
        for job_id in ids:
            store.store_embedding(job_id, vec)

        assert store.count_jobs_needing_embeddings() == 0
        store.clear_stripped_descriptions()

        count = store.conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE embedding IS NOT NULL"
        ).fetchone()["cnt"]
        assert count == 0

    def test_delete_embedding(self, store):
        """delete_embedding clears the embedding column."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        vec = self._make_embedding()

        store.store_embedding(job_id, vec)
        store.delete_embedding(job_id)
        assert store.count_jobs_needing_embeddings() == 1

    def test_search_similar(self, store):
        """search_similar returns jobs ranked by cosine distance."""
        ids = self._insert_jobs_with_stripped(store, "AI engineer role", "Marketing manager role")

        vec1 = [1.0] + [0.0] * 1535
        vec2 = [0.0, 1.0] + [0.0] * 1534

        store.store_embedding(ids[0], vec1)
        store.store_embedding(ids[1], vec2)

        query_vec = [1.0] + [0.0] * 1535

        results = store.search_similar(query_vec, k=2)
        assert len(results) == 2
        assert results[0]["job_id"] == "1"
        assert results[0]["id"] == ids[0]
        assert "distance" in results[0]
        assert "title" in results[0]
        assert "last_sync" in results[0]

    def test_search_similar_empty(self, store):
        """search_similar returns empty list when no embeddings exist."""
        query_vec = [0.1] * 1536
        results = store.search_similar(query_vec, k=5)
        assert results == []

    def test_search_similar_filtered_by_company(self, store):
        """search_similar_filtered respects company filter."""
        store.upsert_jobs("acme", [make_job("1", "PM", "Seattle", description="Lead product.")])
        store.upsert_jobs("beta", [make_job("2", "PM", "Seattle", description="Lead product.")])
        for slug, jid in [("acme", "1"), ("beta", "2")]:
            store.conn.execute(
                "UPDATE jobs SET description_stripped = 'stripped' WHERE company_slug = %s AND job_id = %s",
                (slug, jid),
            )

        vec = [0.1] * 1536
        for jid_str in ["1", "2"]:
            row = store.conn.execute("SELECT id FROM jobs WHERE job_id = %s", (jid_str,)).fetchone()
            store.store_embedding(row["id"], vec)

        results = store.search_similar_filtered(vec, company="acme", k=10)
        assert len(results) == 1
        assert results[0]["company_slug"] == "acme"

    def test_search_similar_filtered_by_title(self, store):
        """search_similar_filtered respects title filter."""
        ids = self._insert_jobs_with_stripped(store, "AI engineer desc", "Marketing mgr desc")
        store.conn.execute("UPDATE jobs SET title = 'AI Engineer' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET title = 'Marketing Manager' WHERE job_id = '2'")

        vec = [0.1] * 1536
        for job_id in ids:
            store.store_embedding(job_id, vec)

        results = store.search_similar_filtered(vec, title="engineer", k=10)
        assert len(results) == 1
        assert results[0]["title"] == "AI Engineer"

    def test_search_similar_filtered_by_location(self, store):
        """search_similar_filtered respects location filter."""
        ids = self._insert_jobs_with_stripped(store, "desc A", "desc B")
        store.conn.execute("UPDATE jobs SET location = 'Seattle, WA' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET location = 'Remote' WHERE job_id = '2'")

        vec = [0.1] * 1536
        for job_id in ids:
            store.store_embedding(job_id, vec)

        results = store.search_similar_filtered(vec, location="remote", k=10)
        assert len(results) == 1
        assert results[0]["location"] == "Remote"

    def test_search_similar_filtered_excludes_disappeared(self, store):
        """search_similar_filtered excludes disappeared jobs."""
        ids = self._insert_jobs_with_stripped(store, "desc A")
        vec = [0.1] * 1536
        store.store_embedding(ids[0], vec)

        store.upsert_jobs("acme", [])

        results = store.search_similar_filtered(vec, k=10)
        assert len(results) == 0

    def test_search_similar_filtered_returns_correct_columns(self, store):
        """search_similar_filtered returns correct columns."""
        ids = self._insert_jobs_with_stripped(store, "desc A")
        vec = [0.1] * 1536
        store.store_embedding(ids[0], vec)

        results = store.search_similar_filtered(vec, k=10)
        assert len(results) == 1
        row = results[0]
        assert row["job_id"] == "1"
        assert row["id"] == ids[0]
        assert "distance" in row
        assert "last_sync" in row
        assert "company_slug" in row

    def test_check_constraint_rejects_empty_stripped_description(self, store):
        """DB CHECK constraint prevents description_stripped='' from being stored."""
        store.upsert_jobs("acme", [make_job("1", description="full desc")])
        with pytest.raises(psycopg.errors.CheckViolation):
            store.conn.execute(
                "UPDATE jobs SET description_stripped = '' WHERE job_id = '1'"
            )
        # Roll back the failed transaction so subsequent operations work
        store.conn.rollback()

    def test_update_stripped_description_rejects_empty(self, store):
        """update_stripped_description stores NULL instead of empty string."""
        ids = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        store.update_stripped_description(job_id, "")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_update_stripped_description_rejects_whitespace_only(self, store):
        """update_stripped_description stores NULL for whitespace-only strings."""
        ids = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        store.update_stripped_description(job_id, "   \n\t  ")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_list_needing_embeddings_limit_skips_already_embedded(self, store):
        """LIMIT applies to jobs that actually need embeddings."""
        ids = self._insert_jobs_with_stripped(
            store, "desc A", "desc B", "desc C", "desc D", "desc E"
        )

        vec = self._make_embedding()
        store.store_embedding(ids[0], vec)
        store.store_embedding(ids[1], vec)

        assert store.count_jobs_needing_embeddings() == 3

        jobs = store.list_jobs_needing_embeddings(limit=3)
        assert len(jobs) == 3
