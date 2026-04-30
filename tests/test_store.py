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

    def test_job_embeddings_table_exists(self, store):
        """job_embeddings table exists with expected columns."""
        rows = store.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'job_embeddings'
            ORDER BY ordinal_position
        """).fetchall()
        cols = {r["column_name"] for r in rows}
        assert {"id", "job_id", "chunk_index", "job_hash", "company_hash", "embedding"} <= cols

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
                "UPDATE jobs SET description_stripped = description WHERE job_id = %s", (jid,)
            )

    def test_fts_stemming(self, store):
        """'engineer' matches 'Engineering' and 'Engineers' via stemming."""
        rows = store.query_jobs(title="engineer")
        titles = {r["title"] for r in rows}
        assert "Software Engineer" in titles
        assert "Software Development Engineer" in titles
        assert "Senior Engineering Manager" in titles

    def test_fts_searches_description(self, store):
        """FTS searches description_stripped, not just title."""
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


# ---------------------------------------------------------------------------
# Embeddings (pgvector)
# ---------------------------------------------------------------------------


class TestEmbeddings:

    def _insert_jobs_with_stripped(self, store, *stripped_descs):
        """Insert jobs with description_stripped set. Returns (ids, job_hashes, company_hash)."""
        jobs = [
            make_job(str(i + 1), description=f"full desc {i}")
            for i, desc in enumerate(stripped_descs)
        ]
        store.upsert_jobs("acme", jobs)
        for i, desc in enumerate(stripped_descs):
            store.conn.execute(
                "UPDATE jobs SET description_stripped = %s WHERE job_id = %s",
                (desc, str(i + 1)),
            )
        # Recompute content_hash with stripped description
        store.conn.execute("""
            UPDATE jobs SET content_hash = md5(
                coalesce(description_stripped, '') || title || coalesce(location, '') || coalesce(department, '')
            )::uuid
            WHERE description_stripped IS NOT NULL
        """)
        ids = []
        job_hashes = []
        for i in range(len(stripped_descs)):
            row = store.conn.execute(
                "SELECT id, content_hash FROM jobs WHERE job_id = %s", (str(i + 1),)
            ).fetchone()
            ids.append(row["id"])
            job_hashes.append(str(row["content_hash"]))
        company_hash = str(store.conn.execute(
            "SELECT content_hash FROM companies WHERE slug = 'acme'"
        ).fetchone()["content_hash"])
        return ids, job_hashes, company_hash

    def _make_embedding(self, dims=1536, val=0.1):
        """Create an embedding as a list of floats."""
        return [val] * dims

    def test_jobs_needing_embeddings_count(self, store):
        """Jobs with description_stripped need embeddings; those without don't."""
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

    def test_list_returns_hash_fields(self, store):
        """list_jobs_needing_embeddings returns job_hash and company_hash."""
        self._insert_jobs_with_stripped(store, "Build AI.")
        jobs = store.list_jobs_needing_embeddings()
        assert len(jobs) == 1
        assert "job_hash" in jobs[0]
        assert "company_hash" in jobs[0]

    def test_count_and_list_consistency(self, store):
        """Count and list modes return consistent results."""
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        count = store.count_jobs_needing_embeddings()
        jobs = store.list_jobs_needing_embeddings()
        assert count == len(jobs)

    def test_store_embedding_makes_job_not_needing(self, store):
        """After storing an embedding, the job no longer needs one."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "Build AI.")
        assert store.count_jobs_needing_embeddings() == 1

        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        assert store.count_jobs_needing_embeddings() == 0

    def test_store_embeddings_batch(self, store):
        """store_embeddings stores multiple embeddings in one call."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(
            store, "Build AI.", "Lead teams."
        )
        assert store.count_jobs_needing_embeddings() == 2

        vec = self._make_embedding()
        store.store_embeddings([
            (ids[0], job_hashes[0], company_hash, vec),
            (ids[1], job_hashes[1], company_hash, vec),
        ])
        assert store.count_jobs_needing_embeddings() == 0

    def test_update_stripped_description_triggers_reembed(self, store):
        """Updating stripped description changes content_hash, causing hash mismatch."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "version 1")
        vec = self._make_embedding()

        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        assert store.count_jobs_needing_embeddings() == 0

        store.update_stripped_description(ids[0], "version 2")
        assert store.count_jobs_needing_embeddings() == 1

    def test_job_hash_change_triggers_reembed(self, store):
        """Changing a job's content_hash while embedding has old hash → needs re-embed."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "desc A")
        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        assert store.count_jobs_needing_embeddings() == 0

        # Simulate title change that updates content_hash
        store.conn.execute("""
            UPDATE jobs SET title = 'New Title',
                content_hash = md5(
                    coalesce(description_stripped, '') || 'New Title' || coalesce(location, '') || coalesce(department, '')
                )::uuid
            WHERE id = %s
        """, (ids[0],))
        assert store.count_jobs_needing_embeddings() == 1

    def test_company_hash_change_triggers_reembed(self, store):
        """Changing company's content_hash while embedding has old hash → needs re-embed."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "desc A")
        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        assert store.count_jobs_needing_embeddings() == 0

        # Update company name → new content_hash
        store.conn.execute(
            "UPDATE companies SET name = 'New Name', content_hash = md5('New Name')::uuid WHERE slug = 'acme'"
        )
        assert store.count_jobs_needing_embeddings() == 1

    def test_clear_stripped_descriptions_deletes_embeddings(self, store):
        """Clearing all stripped descriptions also clears all embeddings."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        vec = self._make_embedding()
        for i in range(len(ids)):
            store.store_embedding(ids[i], job_hashes[i], company_hash, vec)

        assert store.count_jobs_needing_embeddings() == 0
        store.clear_stripped_descriptions()

        count = store.conn.execute(
            "SELECT COUNT(*) AS cnt FROM job_embeddings"
        ).fetchone()["cnt"]
        assert count == 0

    def test_reupsert_after_embed_does_not_trigger_reembed(self, store):
        """Re-upserting the same jobs (fetch phase) after embedding must not invalidate embeddings.

        Regression test: the fetch upsert was computing content_hash with
        description_stripped=None, overwriting the hash that included the
        stripped description. This caused a hash mismatch with job_embeddings.
        """
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "Build AI.")
        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        assert store.count_jobs_needing_embeddings() == 0

        # Simulate fetch phase re-upserting the same job data
        store.upsert_jobs("acme", [make_job("1", description="Build AI.")])
        assert store.count_jobs_needing_embeddings() == 0, \
            "Re-upserting unchanged job data should not invalidate existing embeddings"

    def test_delete_embedding(self, store):
        """delete_embedding removes from job_embeddings table."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "Build AI.")
        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        store.delete_embedding(ids[0])
        assert store.count_jobs_needing_embeddings() == 1

    def test_search_similar(self, store):
        """search_similar returns jobs ranked by cosine distance."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(
            store, "AI engineer role", "Marketing manager role"
        )

        vec1 = [1.0] + [0.0] * 1535
        vec2 = [0.0, 1.0] + [0.0] * 1534

        store.store_embedding(ids[0], job_hashes[0], company_hash, vec1)
        store.store_embedding(ids[1], job_hashes[1], company_hash, vec2)

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
        ids, _, _ = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        store.update_stripped_description(job_id, "")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_update_stripped_description_rejects_whitespace_only(self, store):
        """update_stripped_description stores NULL for whitespace-only strings."""
        ids, _, _ = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        store.update_stripped_description(job_id, "   \n\t  ")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_list_needing_embeddings_limit_skips_already_embedded(self, store):
        """LIMIT applies to jobs that actually need embeddings."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(
            store, "desc A", "desc B", "desc C", "desc D", "desc E"
        )

        vec = self._make_embedding()
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)
        store.store_embedding(ids[1], job_hashes[1], company_hash, vec)

        assert store.count_jobs_needing_embeddings() == 3

        jobs = store.list_jobs_needing_embeddings(limit=3)
        assert len(jobs) == 3

    def test_constraint_trigger_rejects_inconsistent_hashes(self, store):
        """Deferred constraint trigger rejects mismatched hashes for same job_id."""
        ids, job_hashes, company_hash = self._insert_jobs_with_stripped(store, "desc A")
        vec = self._make_embedding()
        # Insert chunk_index=0
        store.store_embedding(ids[0], job_hashes[0], company_hash, vec)

        # Try inserting chunk_index=1 with a different job_hash
        with pytest.raises(psycopg.errors.RaiseException):
            with store.conn.transaction():
                store.conn.execute(
                    """INSERT INTO job_embeddings (job_id, chunk_index, job_hash, company_hash, embedding)
                       VALUES (%s, 1, %s, %s, %s)""",
                    (ids[0], "00000000-0000-0000-0000-000000000000", company_hash, vec),
                )
