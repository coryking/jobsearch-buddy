"""Tests for jobbuddy.store — JobStore class."""

import struct
from datetime import datetime, timezone

import numpy as np
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

    def _get_text_hash(self, job_id_str, desc):
        """Compute the actual text_hash for a job's embed_text."""
        import hashlib
        job = _make_job(job_id_str, description=desc)
        text = job.embed_text("acme", description_stripped=desc)
        return hashlib.sha256(text.encode()).hexdigest()

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
        count = store.jobs_needing_embeddings(count_only=True)
        assert count == 2

    def test_jobs_needing_embeddings_list(self, store):
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        jobs = store.jobs_needing_embeddings(count_only=False)
        assert len(jobs) == 2
        assert all("id" in j and "job_id" in j for j in jobs)

    def test_count_and_list_consistency(self, store):
        """Count and list modes return consistent results."""
        self._insert_jobs_with_stripped(store, "Build AI.", "Lead teams.")
        count = store.jobs_needing_embeddings(count_only=True)
        jobs = store.jobs_needing_embeddings(count_only=False)
        assert count == len(jobs)

    def test_store_embedding_dual_write(self, store):
        """store_embedding writes to both job_embeddings and vec_jobs."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        blob = self._make_embedding()
        text_hash = self._get_text_hash("1", "Build AI.")

        store.store_embedding(job_id, blob, text_hash)

        # Check job_embeddings
        row = store.conn.execute(
            "SELECT * FROM job_embeddings WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None
        assert row["text_hash"] == text_hash

        # Check vec_jobs has the entry
        vec_row = store.conn.execute(
            "SELECT * FROM vec_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert vec_row is not None

    def test_store_embedding_makes_job_not_needing(self, store):
        """After storing an embedding with correct hash, the job no longer needs one."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        assert store.jobs_needing_embeddings(count_only=True) == 1

        blob = self._make_embedding()
        text_hash = self._get_text_hash("1", "Build AI.")

        store.store_embedding(job_id, blob, text_hash)
        assert store.jobs_needing_embeddings(count_only=True) == 0

    def test_hash_mismatch_triggers_re_embedding(self, store):
        """Changed description hash means the job needs re-embedding."""
        ids = self._insert_jobs_with_stripped(store, "version 1")
        job_id = ids[0]
        blob = self._make_embedding()

        store.store_embedding(job_id, blob, "old_hash")
        # The text_hash won't match the actual embed_text hash
        jobs = store.jobs_needing_embeddings(count_only=False)
        assert len(jobs) == 1  # needs re-embedding because hash doesn't match

    def test_delete_embedding(self, store):
        """delete_embedding removes from both job_embeddings and vec_jobs."""
        ids = self._insert_jobs_with_stripped(store, "Build AI.")
        job_id = ids[0]
        blob = self._make_embedding()
        text_hash = self._get_text_hash("1", "Build AI.")

        store.store_embedding(job_id, blob, text_hash)
        store.delete_embedding(job_id)
        assert store.jobs_needing_embeddings(count_only=True) == 1

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

        store.store_embedding(ids[0], blob1, "hash1")
        store.store_embedding(ids[1], blob2, "hash2")

        # Query with vec similar to job 1
        query_vec = [1.0] + [0.0] * 1535
        query_blob = struct.pack(f"<1536f", *query_vec)

        results = store.search_similar(query_blob, k=2)
        assert len(results) == 2
        # First result should be the closer one (job 1)
        assert results[0]["job_id"] == ids[0]
        assert "distance" in results[0]
        assert "title" in results[0]

    def test_search_similar_empty(self, store):
        """search_similar returns empty list when no embeddings exist."""
        query_vec = [0.1] * 1536
        query_blob = struct.pack(f"<1536f", *query_vec)
        results = store.search_similar(query_blob, k=5)
        assert results == []



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
