"""Tests for jobbuddy.store — JobStore class."""

import struct
from datetime import datetime, timezone

import pytest

from jobbuddy.models import Job
from jobbuddy.store import JobStore


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
def store():
    """In-memory JobStore."""
    s = JobStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_jobs_table_has_surrogate_key(self, store):
        """jobs table uses INTEGER PRIMARY KEY AUTOINCREMENT (id column)."""
        cols = {row[1]: row[2] for row in store.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "id" in cols
        assert cols["id"] == "INTEGER"

    def test_jobs_has_unique_constraint(self, store):
        """(company_slug, job_id) is UNIQUE."""
        indexes = store.conn.execute("PRAGMA index_list(jobs)").fetchall()
        unique_indexes = [idx for idx in indexes if idx[2] == 1]  # non_unique=0 means unique
        assert len(unique_indexes) >= 1

    def test_embedding_models_table_removed(self, store):
        """embedding_models table no longer exists (single-model schema)."""
        tables = {row[0] for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "embedding_models" not in tables

    def test_job_embeddings_table_exists(self, store):
        tables = {row[0] for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "job_embeddings" in tables

    def test_vec_jobs_table_exists(self, store):
        """vec0 virtual table for cosine similarity search."""
        # vec0 tables show up in sqlite_master
        row = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'vec_jobs'"
        ).fetchone()
        assert row is not None

    def test_foreign_keys_enabled(self, store):
        fk = store.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_context_manager(self):
        """JobStore works as a context manager."""
        with JobStore(":memory:") as store:
            store.upsert_jobs("acme", [_make_job("1")])
            assert store.job_count() == 1


# ---------------------------------------------------------------------------
# Upsert + Query
# ---------------------------------------------------------------------------


class TestUpsertAndQuery:
    def test_upsert_inserts_jobs(self, store):
        jobs = [_make_job("1", "PM", "Seattle"), _make_job("2", "SWE", "Remote")]
        store.upsert_jobs("acme", jobs)
        rows = store.query_jobs()
        assert len(rows) == 2

    def test_upsert_replaces_on_resync(self, store):
        store.upsert_jobs("acme", [_make_job("1", "PM", "Seattle")])
        store.upsert_jobs("acme", [_make_job("1", "PM Updated", "NYC")])
        rows = store.query_jobs(company="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM Updated"
        assert rows[0]["location"] == "NYC"

    def test_upsert_marks_disappeared_jobs(self, store):
        store.upsert_jobs("acme", [_make_job("1"), _make_job("2")])
        assert len(store.query_jobs()) == 2
        store.upsert_jobs("acme", [_make_job("1")])
        assert len(store.query_jobs()) == 1
        assert len(store.query_jobs(include_disappeared=True)) == 2

    def test_disappeared_job_reappears(self, store):
        store.upsert_jobs("acme", [_make_job("1"), _make_job("2")])
        store.upsert_jobs("acme", [_make_job("1")])
        assert len(store.query_jobs()) == 1
        store.upsert_jobs("acme", [_make_job("1"), _make_job("2")])
        assert len(store.query_jobs()) == 2

    def test_upsert_deduplicates_input(self, store):
        dupes = [_make_job("1", "PM v1"), _make_job("1", "PM v2")]
        store.upsert_jobs("acme", dupes)
        rows = store.query_jobs(company="acme")
        assert len(rows) == 1
        assert rows[0]["title"] == "PM v2"

    def test_upsert_isolates_companies(self, store):
        store.upsert_jobs("acme", [_make_job("1")])
        store.upsert_jobs("beta", [_make_job("2"), _make_job("3")])
        store.upsert_jobs("acme", [])
        assert len(store.query_jobs()) == 2

    def test_null_description_preserves_existing(self, store):
        """Re-syncing with NULL description keeps previously-enriched description."""
        store.upsert_jobs("acme", [_make_job("1", description="enriched")])
        store.upsert_jobs("acme", [_make_job("1")])
        rows = store.query_jobs(company="acme")
        assert rows[0]["description"] == "enriched"

    def test_surrogate_key_assigned(self, store):
        """Jobs get an integer surrogate key (id)."""
        store.upsert_jobs("acme", [_make_job("1")])
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
            _make_job("1", "Product Manager", "Seattle, WA", salary="$150k"),
            _make_job("2", "Software Engineer", "Remote", team="Platform"),
            _make_job("3", "Senior PM", "New York, NY"),
            _make_job("4", "Data Scientist", "Seattle, WA"),
        ])
        store.upsert_jobs("beta", [
            _make_job("5", "Product Manager", "London"),
            _make_job("6", "Designer", "Remote"),
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
            _make_job("1", description="has one"),
            _make_job("2"),
            _make_job("3"),
        ])
        needing = store.get_jobs_needing_descriptions("acme")
        ids = {j["job_id"] for j in needing}
        assert ids == {"2", "3"}

    def test_update_descriptions(self, store):
        store.upsert_jobs("acme", [_make_job("1"), _make_job("2")])
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
        store.upsert_jobs("acme", [_make_job("1")])
        assert store.is_stale("acme", 24) is False

    def test_record_sync_error(self, store):
        store.record_sync_error("broken-co", "Connection timeout")
        row = store.conn.execute(
            "SELECT * FROM sync_status WHERE company_slug = ?", ("broken-co",)
        ).fetchone()
        assert row["error"] == "Connection timeout"


# ---------------------------------------------------------------------------
# Embeddings (single-model, sqlite-vec)
# ---------------------------------------------------------------------------


class TestEmbeddings:
    """Tests for the single-model embedding store with sqlite-vec vec0 table."""

    def _insert_jobs_with_stripped(self, store, *stripped_descs):
        """Insert jobs with description_stripped set (the gate for needing embeddings)."""
        jobs = [
            _make_job(str(i + 1), description=f"full desc {i}", description_stripped=desc)
            for i, desc in enumerate(stripped_descs)
        ]
        store.upsert_jobs("acme", jobs)
        # Set description_stripped via direct SQL since upsert doesn't handle it
        for i, desc in enumerate(stripped_descs):
            store.conn.execute(
                "UPDATE jobs SET description_stripped = ? WHERE job_id = ?",
                (desc, str(i + 1)),
            )
        store.conn.commit()
        ids = []
        for i in range(len(stripped_descs)):
            row = store.conn.execute(
                "SELECT id FROM jobs WHERE job_id = ?", (str(i + 1),)
            ).fetchone()
            ids.append(row["id"])
        return ids

    def _make_embedding(self, dims=1536, val=0.1):
        """Create a float32 embedding blob."""
        vec = [val] * dims
        return struct.pack(f"<{len(vec)}f", *vec)

    def test_jobs_needing_embeddings_count(self, store):
        """Jobs with description_stripped need embeddings; those without don't."""
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        # Add a third job with no description_stripped
        store.upsert_jobs("acme", [
            _make_job("1", description="Build AI."),
            _make_job("2", description="Lead teams."),
            _make_job("3"),  # no description or stripped
        ])
        # Re-set stripped on 1 and 2
        store.conn.execute("UPDATE jobs SET description_stripped = 'Build AI.' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET description_stripped = 'Lead teams.' WHERE job_id = '2'")
        store.conn.commit()
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

    def test_store_embedding_dual_write(self, store):
        """store_embedding writes to both job_embeddings and vec_jobs."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        blob = self._make_embedding()

        store.store_embedding(job_id, blob)

        # Check job_embeddings
        row = store.conn.execute(
            "SELECT * FROM job_embeddings WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None

        # Check vec_jobs has the entry
        vec_row = store.conn.execute(
            "SELECT * FROM vec_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert vec_row is not None

    def test_store_embedding_makes_job_not_needing(self, store):
        """After storing an embedding, the job no longer needs one."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        assert store.count_jobs_needing_embeddings() == 1

        blob = self._make_embedding()
        store.store_embedding(job_id, blob)
        assert store.count_jobs_needing_embeddings() == 0

    def test_update_stripped_description_deletes_embedding(self, store):
        """Updating stripped description cascades: deletes the stale embedding."""
        ids = self._insert_jobs_with_stripped(store, "version 1")
        job_id = ids[0]
        blob = self._make_embedding()

        store.store_embedding(job_id, blob)
        assert store.count_jobs_needing_embeddings() == 0

        # Re-strip triggers re-embed
        store.update_stripped_description(job_id, "version 2")
        assert store.count_jobs_needing_embeddings() == 1

        # Both tables should be cleaned
        emb_row = store.conn.execute(
            "SELECT * FROM job_embeddings WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert emb_row is None
        vec_row = store.conn.execute(
            "SELECT * FROM vec_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert vec_row is None

    def test_clear_stripped_descriptions_deletes_embeddings(self, store):
        """Clearing all stripped descriptions also deletes all embeddings."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        blob = self._make_embedding()
        for job_id in ids:
            store.store_embedding(job_id, blob)

        assert store.count_jobs_needing_embeddings() == 0
        store.clear_stripped_descriptions()

        # Embeddings should be gone
        count = store.conn.execute("SELECT COUNT(*) FROM job_embeddings").fetchone()[0]
        assert count == 0
        vec_count = store.conn.execute("SELECT COUNT(*) FROM vec_jobs").fetchone()[0]
        assert vec_count == 0

    def test_delete_embedding(self, store):
        """delete_embedding removes from both job_embeddings and vec_jobs."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        blob = self._make_embedding()

        store.store_embedding(job_id, blob)
        store.delete_embedding(job_id)
        assert store.count_jobs_needing_embeddings() == 1

        # vec_jobs should also be empty
        vec_row = store.conn.execute(
            "SELECT * FROM vec_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert vec_row is None

    def test_search_similar(self, store):
        """search_similar returns jobs ranked by cosine distance."""
        ids = self._insert_jobs_with_stripped(store, "AI engineer role", "Marketing manager role")

        # Create two distinct embeddings
        vec1 = [1.0] + [0.0] * 1535
        blob1 = struct.pack(f"<1536f", *vec1)
        vec2 = [0.0, 1.0] + [0.0] * 1534
        blob2 = struct.pack(f"<1536f", *vec2)

        store.store_embedding(ids[0], blob1)
        store.store_embedding(ids[1], blob2)

        # Query with vec similar to job 1
        query_vec = [1.0] + [0.0] * 1535
        query_blob = struct.pack(f"<1536f", *query_vec)

        results = store.search_similar(query_blob, k=2)
        assert len(results) == 2
        # First result should be the closer one (job 1)
        # job_id is the ATS string ("1"), id is the surrogate PK
        assert results[0]["job_id"] == "1"
        assert results[0]["id"] == ids[0]
        assert "distance" in results[0]
        assert "title" in results[0]
        assert "last_sync" in results[0]

    def test_search_similar_empty(self, store):
        """search_similar returns empty list when no embeddings exist."""
        query_vec = [0.1] * 1536
        query_blob = struct.pack(f"<1536f", *query_vec)
        results = store.search_similar(query_blob, k=5)
        assert results == []

    def test_search_similar_filtered_by_company(self, store):
        """search_similar_filtered respects company filter."""
        store.upsert_jobs("acme", [_make_job("1", "PM", "Seattle", description="Lead product.")])
        store.upsert_jobs("beta", [_make_job("2", "PM", "Seattle", description="Lead product.")])
        for slug, jid in [("acme", "1"), ("beta", "2")]:
            store.conn.execute(
                "UPDATE jobs SET description_stripped = 'stripped' WHERE company_slug = ? AND job_id = ?",
                (slug, jid),
            )
        store.conn.commit()

        vec = [0.1] * 1536
        blob = struct.pack(f"<1536f", *vec)
        for jid_str in ["1", "2"]:
            row = store.conn.execute("SELECT id FROM jobs WHERE job_id = ?", (jid_str,)).fetchone()
            store.store_embedding(row["id"], blob)

        query_blob = struct.pack(f"<1536f", *vec)
        results = store.search_similar_filtered(query_blob, company="acme", k=10)
        assert len(results) == 1
        assert results[0]["company_slug"] == "acme"

    def test_search_similar_filtered_by_title(self, store):
        """search_similar_filtered respects title filter."""
        ids = self._insert_jobs_with_stripped(store, "AI engineer desc", "Marketing mgr desc")
        # Set distinct titles
        store.conn.execute("UPDATE jobs SET title = 'AI Engineer' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET title = 'Marketing Manager' WHERE job_id = '2'")
        store.conn.commit()

        vec = [0.1] * 1536
        blob = struct.pack(f"<1536f", *vec)
        for job_id in ids:
            store.store_embedding(job_id, blob)

        query_blob = struct.pack(f"<1536f", *vec)
        results = store.search_similar_filtered(query_blob, title="engineer", k=10)
        assert len(results) == 1
        assert results[0]["title"] == "AI Engineer"

    def test_search_similar_filtered_by_location(self, store):
        """search_similar_filtered respects location filter."""
        ids = self._insert_jobs_with_stripped(store, "desc A", "desc B")
        store.conn.execute("UPDATE jobs SET location = 'Seattle, WA' WHERE job_id = '1'")
        store.conn.execute("UPDATE jobs SET location = 'Remote' WHERE job_id = '2'")
        store.conn.commit()

        vec = [0.1] * 1536
        blob = struct.pack(f"<1536f", *vec)
        for job_id in ids:
            store.store_embedding(job_id, blob)

        query_blob = struct.pack(f"<1536f", *vec)
        results = store.search_similar_filtered(query_blob, location="remote", k=10)
        assert len(results) == 1
        assert results[0]["location"] == "Remote"

    def test_search_similar_filtered_excludes_disappeared(self, store):
        """search_similar_filtered excludes disappeared jobs."""
        ids = self._insert_jobs_with_stripped(store, "desc A")
        vec = [0.1] * 1536
        blob = struct.pack(f"<1536f", *vec)
        store.store_embedding(ids[0], blob)

        # Disappear the job
        store.upsert_jobs("acme", [])

        query_blob = struct.pack(f"<1536f", *vec)
        results = store.search_similar_filtered(query_blob, k=10)
        assert len(results) == 0

    def test_search_similar_filtered_returns_correct_columns(self, store):
        """search_similar_filtered returns j.* columns (job_id as ATS string, id as surrogate)."""
        ids = self._insert_jobs_with_stripped(store, "desc A")
        vec = [0.1] * 1536
        blob = struct.pack(f"<1536f", *vec)
        store.store_embedding(ids[0], blob)

        query_blob = struct.pack(f"<1536f", *vec)
        results = store.search_similar_filtered(query_blob, k=10)
        assert len(results) == 1
        row = results[0]
        assert row["job_id"] == "1"  # ATS string, not surrogate PK
        assert row["id"] == ids[0]   # surrogate PK
        assert "distance" in row
        assert "last_sync" in row
        assert "company_slug" in row

    def test_check_constraint_rejects_empty_stripped_description(self, store):
        """DB CHECK constraint prevents description_stripped='' from being stored.

        Regression: empty strings from failed LLM calls passed IS NOT NULL checks
        but produced no embed text, causing infinite silent retry loops.
        """
        import sqlite3

        store.upsert_jobs("acme", [_make_job("1", description="full desc")])
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "UPDATE jobs SET description_stripped = '' WHERE job_id = '1'"
            )

    def test_update_stripped_description_rejects_empty(self, store):
        """update_stripped_description stores NULL instead of empty string.

        Ensures failed LLM responses don't poison the pipeline.
        """
        ids = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        # Writing empty string should store NULL instead
        store.update_stripped_description(job_id, "")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_update_stripped_description_rejects_whitespace_only(self, store):
        """update_stripped_description stores NULL for whitespace-only strings."""
        ids = self._insert_jobs_with_stripped(store, "real content")
        job_id = ids[0]

        store.update_stripped_description(job_id, "   \n\t  ")
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["description_stripped"] is None

    def test_list_needing_embeddings_limit_skips_already_embedded(self, store):
        """LIMIT applies to jobs that actually need embeddings, not all jobs.

        Regression test: old code applied LIMIT at the SQL level to all jobs
        with stripped descriptions, then filtered out already-embedded jobs in
        Python. After the first batch was embedded, poll_work returned [] and
        the embed phase hung forever.
        """
        # Create 5 jobs with stripped descriptions
        ids = self._insert_jobs_with_stripped(
            store, "desc A", "desc B", "desc C", "desc D", "desc E"
        )

        # Embed jobs 1 and 2
        blob = self._make_embedding()
        store.store_embedding(ids[0], blob)
        store.store_embedding(ids[1], blob)

        # Verify: 3 jobs still need embeddings
        assert store.count_jobs_needing_embeddings() == 3

        # With limit=3, we should get all 3 remaining jobs
        jobs = store.list_jobs_needing_embeddings(limit=3)
        assert len(jobs) == 3



# ---------------------------------------------------------------------------
# Migration from old schema
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_from_old_schema(self, tmp_path):
        """Opening a DB with old composite-PK schema migrates to surrogate key."""
        import sqlite3

        db_path = tmp_path / "old.db"
        # Create old-style schema
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE jobs (
                company_slug TEXT NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                url TEXT,
                published_at DATE,
                department TEXT,
                team TEXT,
                salary TEXT,
                description TEXT,
                ats_metadata TEXT,
                last_seen TIMESTAMP NOT NULL,
                disappeared_at TIMESTAMP,
                PRIMARY KEY (company_slug, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE sync_status (
                company_slug TEXT PRIMARY KEY,
                last_sync TIMESTAMP NOT NULL,
                job_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            )
        """)
        # Insert test data
        conn.execute(
            "INSERT INTO jobs VALUES ('acme', '1', 'PM', 'Seattle', 'https://x', NULL, NULL, NULL, NULL, 'desc', NULL, '2025-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO sync_status VALUES ('acme', '2025-01-01', 1, NULL)"
        )
        conn.commit()
        conn.close()

        # Open with JobStore — should migrate
        store = JobStore(str(db_path))

        # Verify surrogate key exists
        cols = {row[1] for row in store.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "id" in cols

        # Verify data preserved
        row = store.conn.execute("SELECT * FROM jobs WHERE job_id = '1'").fetchone()
        assert row["title"] == "PM"
        assert row["description"] == "desc"
        assert row["id"] is not None

        store.close()
