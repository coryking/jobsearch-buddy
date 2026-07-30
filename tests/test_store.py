"""Tests for jobbuddy.store -- JobStore class (PostgreSQL)."""

from datetime import datetime, timedelta, timezone

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

    def test_upsert_locks_content_at_first_insert(self, store):
        """Pure-insert model: a job's content (title, location, etc.) is
        fixed at first insert. Re-syncing with different values does NOT
        overwrite — fixes for fetcher parsing bugs require an explicit
        backfill, not a side-effect of routine sync."""
        store.upsert_jobs("acme", [make_job("1", "PM", "Seattle")])
        store.upsert_jobs("acme", [make_job("1", "PM Updated", "NYC")])
        rows = store.query_jobs(companies=["acme"])
        assert len(rows) == 1
        assert rows[0]["title"] == "PM"
        assert rows[0]["location"] == "Seattle"

    def test_last_listing_update_writes_on_insert(self, store):
        """Fetcher-supplied last_listing_update lands on first insert."""
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-05-01"),
        ])
        row = store.conn.execute(
            "SELECT last_listing_update FROM jobs WHERE company_slug='acme' AND job_id='1'"
        ).fetchone()
        assert row["last_listing_update"].isoformat() == "2026-05-01"

    def test_last_listing_update_advances_on_resync(self, store):
        """Newer ATS-side update overrides the stored value (GREATEST)."""
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-05-01"),
        ])
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-05-09"),
        ])
        row = store.conn.execute(
            "SELECT last_listing_update FROM jobs WHERE company_slug='acme' AND job_id='1'"
        ).fetchone()
        assert row["last_listing_update"].isoformat() == "2026-05-09"

    def test_last_listing_update_keeps_newer_when_older_arrives(self, store):
        """Out-of-order syncs never regress the column (GREATEST keeps max)."""
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-05-09"),
        ])
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-04-01"),
        ])
        row = store.conn.execute(
            "SELECT last_listing_update FROM jobs WHERE company_slug='acme' AND job_id='1'"
        ).fetchone()
        assert row["last_listing_update"].isoformat() == "2026-05-09"

    def test_last_listing_update_null_does_not_clobber(self, store):
        """A fetcher that doesn't surface this field (NULL) preserves the value."""
        store.upsert_jobs("acme", [
            make_job("1", last_listing_update="2026-05-01"),
        ])
        store.upsert_jobs("acme", [make_job("1")])  # no last_listing_update
        row = store.conn.execute(
            "SELECT last_listing_update FROM jobs WHERE company_slug='acme' AND job_id='1'"
        ).fetchone()
        assert row["last_listing_update"].isoformat() == "2026-05-01"

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

    def test_upsert_coerces_null_published_at_to_today(self, store):
        """New rows with NULL published_at (Tesla, fresh stub-fetcher rows
        before enrich runs) get CURRENT_DATE on insert via VALUES COALESCE.
        This makes the column safe to mark NOT NULL — and the pure-insert
        ON CONFLICT path doesn't touch published_at, so existing rows with
        NULL are left alone (the enrich-phase backfill path stays open
        for already-NULL rows)."""
        from datetime import date
        store.upsert_jobs("acme", [make_job("1", published_at=None)])
        row = store.conn.execute(
            "SELECT published_at FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["published_at"] == date.today()

    def test_upsert_preserves_existing_published_at_on_null_resync(self, store):
        """A row that already has a real posted date (from prior sync, or
        from the enrich phase) must not be reset to today just because the
        next sync's stub didn't carry one."""
        from datetime import date
        original = date(2026, 3, 15)
        store.upsert_jobs("acme", [make_job("1", published_at=original)])
        # Re-sync with NULL — should NOT bump to today.
        store.upsert_jobs("acme", [make_job("1", published_at=None)])
        row = store.conn.execute(
            "SELECT published_at FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["published_at"] == original

    def test_upsert_locks_published_at_at_first_insert(self, store):
        """Under pure-insert, a published_at set on first insert is fixed.
        Re-syncing with a different date does NOT overwrite. The enrich
        phase fills NULLs via update_enrichment; that's the only
        sanctioned path for filling a posted date post-insert."""
        from datetime import date
        store.upsert_jobs("acme", [make_job("1", published_at=date(2026, 1, 1))])
        store.upsert_jobs("acme", [make_job("1", published_at=date(2026, 3, 15))])
        row = store.conn.execute(
            "SELECT published_at FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["published_at"] == date(2026, 1, 1)

    def test_upsert_preserves_salary_on_null_resync(self, store):
        """Rippling moving to stub mode means list_jobs() returns salary=None
        and the enrich phase fills it later. Re-sync of the stub must not
        clobber an enriched salary."""
        store.upsert_jobs("acme", [make_job("1", salary="$200k-$300k")])
        store.upsert_jobs("acme", [make_job("1", salary=None)])
        row = store.conn.execute(
            "SELECT salary FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["salary"] == "$200k-$300k"

    def test_description_locked_at_first_insert(self, store):
        """Pure-insert: re-syncing with a different description does NOT
        overwrite, and therefore does NOT invalidate distill outputs.
        Distill runs exactly once per (slug, job_id) lifetime — a
        meaningful description change requires explicit backfill."""
        store.upsert_jobs("acme", [make_job("1", description="v1 body")])
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
        assert row["description"] == "v1 body"
        assert row["short_jd"] == "capsule v1"
        assert row["description_normalized"] == "normalized v1"

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


class TestEffectiveDate:
    """Generated `effective_date = COALESCE(last_listing_update, published_at)`
    drives `posted_after` filtering and ordering. ATSes that surface a
    freshness signal sort by their most recent ATS-side touch; others fall
    back to publish date — same behavior as before for those rows."""

    def test_effective_date_falls_back_to_published_at(self, store):
        store.upsert_jobs("acme", [
            make_job("1", published_at="2026-01-01"),  # no last_listing_update
        ])
        row = store.conn.execute(
            "SELECT effective_date FROM jobs WHERE company_slug='acme'"
        ).fetchone()
        assert row["effective_date"].isoformat() == "2026-01-01"

    def test_effective_date_uses_last_listing_update_when_present(self, store):
        store.upsert_jobs("acme", [
            make_job("1", published_at="2019-01-01",
                     last_listing_update="2026-05-01"),
        ])
        row = store.conn.execute(
            "SELECT effective_date FROM jobs WHERE company_slug='acme'"
        ).fetchone()
        assert row["effective_date"].isoformat() == "2026-05-01"

    def test_posted_after_uses_effective_date(self, store):
        """A 2019 publish date with a 2026 freshness touch is in-window for
        a 2026 `posted_after` filter — the whole reason effective_date exists."""
        store.upsert_jobs("acme", [
            make_job("stale", published_at="2019-01-01"),
            make_job("refreshed", published_at="2019-01-01",
                     last_listing_update="2026-05-01"),
        ])
        rows = store.query_jobs(posted_after="2026-01-01")
        ids = {r["job_id"] for r in rows}
        assert ids == {"refreshed"}

    def test_query_orders_by_freshness_bucket_then_effective_date(self, store):
        """Within a freshness bucket (on published_at), rows order by
        effective_date DESC — so a recently-touched listing still beats a
        non-touched listing of the same publish-age. But across buckets
        the bucket dominates: a stale-published listing (bucket 3) sits
        below any non-stale listing regardless of its ATS-touch date.
        See the freshness-bucket ranking discussion in core/search.py.
        """
        from datetime import date, timedelta

        today = date.today()
        store.upsert_jobs("acme", [
            # Bucket 0 (<=7d), no touch.
            make_job("fresh", published_at=(today - timedelta(days=3)).isoformat()),
            # Bucket 0 (<=7d), recently touched — same bucket as `fresh`,
            # but effective_date is newer, so it sorts above.
            make_job("fresh-touched",
                     published_at=(today - timedelta(days=5)).isoformat(),
                     last_listing_update=today.isoformat()),
            # Bucket 3 (older than 90d), recently touched — drops below
            # the bucket-0 rows even though its effective_date is today.
            make_job("stale-touched",
                     published_at="2019-01-01",
                     last_listing_update=today.isoformat()),
        ])
        rows = store.query_jobs(companies=["acme"])
        assert [r["job_id"] for r in rows] == ["fresh-touched", "fresh", "stale-touched"]


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


class TestEnrichmentQuery:
    """get_jobs_needing_enrichment(slug, columns) generalizes
    get_jobs_needing_descriptions: a row matches if ANY of the requested
    columns is NULL. Lets fetchers re-fetch detail pages for rows that
    have a description but are missing other detail-page fields like
    published_at."""

    from datetime import date

    def test_description_only_matches_null_description(self, store):
        store.upsert_jobs("acme", [
            make_job("1", description="x", published_at=self.date(2026, 1, 1)),
            make_job("2", description=None, published_at=self.date(2026, 1, 1)),
        ])
        needing = store.get_jobs_needing_enrichment("acme", ("description",))
        assert {j["job_id"] for j in needing} == {"2"}

    def test_or_predicate_matches_either_null(self, store):
        """Use description + salary to exercise the OR-of-NULLs predicate.
        Both columns are still nullable post-migration-013; published_at
        is not (the upsert COALESCEs NULLs to today on insert), so it
        can't be used in a "construct a NULL row" scenario."""
        store.upsert_jobs("acme", [
            make_job("1", description="x", salary="$100k"),  # nothing missing
            make_job("2", description=None, salary="$100k"),  # desc missing
            make_job("3", description="x", salary=None),      # salary missing
            make_job("4", description=None, salary=None),     # both missing
        ])
        needing = store.get_jobs_needing_enrichment("acme", ("description", "salary"))
        assert {j["job_id"] for j in needing} == {"2", "3", "4"}

    def test_returns_same_columns_as_legacy(self, store):
        """Enrich phase reads job_id, title, ats_metadata; the new query must
        return the same shape so callers don't have to branch."""
        store.upsert_jobs("acme", [make_job("1", description=None, ats_metadata={"k": "v"})])
        rows = store.get_jobs_needing_enrichment("acme", ("description",))
        assert len(rows) == 1
        assert set(rows[0].keys()) >= {"job_id", "title", "ats_metadata"}

    def test_only_active_listings(self, store):
        """Soft-deleted listings (not in the next sync) must not be enriched."""
        store.upsert_jobs("acme", [make_job("1", description=None), make_job("2", description=None)])
        # Re-sync without job 2 — it gets soft-deleted.
        store.upsert_jobs("acme", [make_job("1", description=None)])
        needing = store.get_jobs_needing_enrichment("acme", ("description",))
        assert {j["job_id"] for j in needing} == {"1"}


class TestUpdateEnrichment:
    """update_enrichment(slug, payloads) writes whatever columns are present
    in each per-job payload dict — generalizes update_descriptions so the
    enrich phase can land description + published_at + salary in one batch."""

    from datetime import date

    def test_writes_multiple_columns(self, store):
        store.upsert_jobs("acme", [make_job("1", description=None, salary=None)])
        store.update_enrichment("acme", {
            "1": {"description": "the desc", "salary": "$100k"},
        })
        row = store.conn.execute(
            "SELECT description, salary FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] == "the desc"
        assert row["salary"] == "$100k"

    def test_partial_payload_only_writes_present_columns(self, store):
        """An enrichment that captured salary but not description must
        leave description alone."""
        store.upsert_jobs("acme", [make_job("1", description="existing", salary=None)])
        store.update_enrichment("acme", {
            "1": {"salary": "$100k"},
        })
        row = store.conn.execute(
            "SELECT description, salary FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] == "existing"
        assert row["salary"] == "$100k"

    def test_empty_payload_dict_is_noop(self, store):
        """An empty payload (detail page yielded nothing) must not blow up."""
        store.upsert_jobs("acme", [make_job("1", description="x")])
        store.update_enrichment("acme", {"1": {}})
        row = store.conn.execute(
            "SELECT description FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] == "x"

    def test_rejects_unknown_columns(self, store):
        """Defense-in-depth: only whitelisted columns can be written."""
        store.upsert_jobs("acme", [make_job("1", description=None)])
        import pytest
        with pytest.raises(ValueError, match="unsupported"):
            store.update_enrichment("acme", {"1": {"id": 999}})

    def test_published_at_preserved_when_existing(self, store):
        """Old-wins: a re-enrich never clobbers an existing populated
        value, regardless of which column it is. Posted dates don't
        change in practice."""
        existing = self.date(2026, 1, 1)
        store.upsert_jobs("acme", [make_job("1", published_at=existing)])
        store.update_enrichment("acme", {
            "1": {"published_at": self.date(2026, 5, 1)},
        })
        row = store.conn.execute(
            "SELECT published_at FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["published_at"] == existing

    def test_description_preserved_when_existing(self, store):
        """Old-wins applies to description: when a row matches the OR
        predicate because of a missing column (e.g. salary), the
        re-fetched description must NOT overwrite the existing one."""
        store.upsert_jobs("acme", [make_job("1", description="original", salary=None)])
        store.update_enrichment("acme", {
            "1": {"description": "re-parsed", "salary": "$100k"},
        })
        row = store.conn.execute(
            "SELECT description, salary FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] == "original"
        assert row["salary"] == "$100k"

    def test_salary_preserved_when_existing(self, store):
        store.upsert_jobs("acme", [make_job("1", salary="$200k")])
        store.update_enrichment("acme", {"1": {"salary": "$300k"}})
        row = store.conn.execute(
            "SELECT salary FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["salary"] == "$200k"

    def test_description_written_when_existing_null(self, store):
        """For stub-fetcher rows that come in with NULL description,
        update_enrichment fills the description."""
        store.upsert_jobs("acme", [make_job("1", description=None)])
        store.update_enrichment("acme", {"1": {"description": "filled"}})
        row = store.conn.execute(
            "SELECT description FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] == "filled"

    def test_mark_listing_removed_flips_status(self, store):
        """A 404 from the ATS detail page means the listing is gone;
        mark_listing_removed flips status and the trigger sets removed_at."""
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.mark_listing_removed("acme", "2")
        row = store.conn.execute(
            "SELECT listing_status, removed_at FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["listing_status"] == "removed"
        assert row["removed_at"] is not None

    def test_mark_listing_removed_idempotent_for_already_removed(self, store):
        """Calling mark_listing_removed on an already-removed row is a no-op."""
        store.upsert_jobs("acme", [make_job("1"), make_job("2")])
        store.upsert_jobs("acme", [make_job("1")])  # job 2 already removed
        store.mark_listing_removed("acme", "2")  # should not raise
        row = store.conn.execute(
            "SELECT listing_status FROM jobs WHERE job_id = '2'"
        ).fetchone()
        assert row["listing_status"] == "removed"

    def test_rollback_on_bad_payload_in_batch(self, store):
        """All-or-nothing: a bad column in any one payload prevents the
        entire batch from writing."""
        store.upsert_jobs("acme", [make_job("1", description=None), make_job("2", description=None)])
        import pytest
        with pytest.raises(ValueError, match="unsupported"):
            store.update_enrichment("acme", {
                "1": {"description": "should not land"},
                "2": {"id": 999},
            })
        row = store.conn.execute(
            "SELECT description FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description"] is None


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
# Company bios (Phase 2 research pipeline)
# ---------------------------------------------------------------------------


class TestCompanyBios:
    def test_companies_table_has_bio_columns(self, store):
        rows = store.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'companies'
        """).fetchall()
        cols = {r["column_name"] for r in rows}
        assert {"short_bio", "long_bio", "bio_researched_at", "bio_model"} <= cols

    def _seeded_count(self, store) -> int:
        row = store.conn.execute("SELECT COUNT(*) AS c FROM companies").fetchone()
        return row["c"]

    def test_count_companies_needing_bio_counts_unfilled(self, store):
        assert store.count_companies_needing_bio() == self._seeded_count(store)

    def test_count_companies_needing_bio_excludes_filled(self, store):
        baseline = self._seeded_count(store)
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="gpt-5.4")
        assert store.count_companies_needing_bio() == baseline - 1

    def test_count_companies_needing_bio_with_slug_filter(self, store):
        assert store.count_companies_needing_bio(slugs=["acme"]) == 1
        assert store.count_companies_needing_bio(slugs=["acme", "beta"]) == 2
        assert store.count_companies_needing_bio(slugs=["nonexistent"]) == 0
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="m")
        assert store.count_companies_needing_bio(slugs=["acme", "beta"]) == 1

    def test_get_companies_needing_bio_returns_slug_and_name(self, store):
        items = store.get_companies_needing_bio(limit=100)
        assert len(items) == self._seeded_count(store)
        assert all(set(i.keys()) == {"slug", "name"} for i in items)
        assert "acme" in {i["slug"] for i in items}

    def test_get_companies_needing_bio_excludes_filled(self, store):
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="gpt-5.4")
        slugs = {i["slug"] for i in store.get_companies_needing_bio(limit=100)}
        assert "acme" not in slugs
        assert len(slugs) == self._seeded_count(store) - 1

    def test_get_companies_needing_bio_respects_limit(self, store):
        items = store.get_companies_needing_bio(limit=2)
        assert len(items) == 2

    def test_get_companies_needing_bio_with_slug_filter(self, store):
        items = store.get_companies_needing_bio(slugs=["acme"])
        assert [i["slug"] for i in items] == ["acme"]

        items = store.get_companies_needing_bio(slugs=["acme", "beta"])
        assert sorted(i["slug"] for i in items) == ["acme", "beta"]

    def test_update_company_bio_writes_all_fields(self, store):
        # bio_researched_at is SQL now() — transaction-start time. The store
        # connection's transaction can open microseconds before this line
        # runs, so the bounds need slop to avoid a sub-millisecond flake.
        slop = timedelta(seconds=2)
        before = datetime.now(timezone.utc) - slop
        store.update_company_bio(
            "acme", short_bio="short", long_bio="long prose", model="gpt-5.4"
        )
        after = datetime.now(timezone.utc) + slop
        row = store.conn.execute(
            "SELECT short_bio, long_bio, bio_researched_at, bio_model"
            " FROM companies WHERE slug = 'acme'"
        ).fetchone()
        assert row["short_bio"] == "short"
        assert row["long_bio"] == "long prose"
        assert row["bio_model"] == "gpt-5.4"
        assert before <= row["bio_researched_at"] <= after

    def test_update_company_bio_overwrites(self, store):
        store.update_company_bio("acme", short_bio="v1", long_bio="v1-long", model="m1")
        store.update_company_bio("acme", short_bio="v2", long_bio="v2-long", model="m2")
        row = store.conn.execute(
            "SELECT short_bio, long_bio, bio_model FROM companies WHERE slug = 'acme'"
        ).fetchone()
        assert row["short_bio"] == "v2"
        assert row["long_bio"] == "v2-long"
        assert row["bio_model"] == "m2"

    def test_save_company_does_not_clobber_bios(self, store):
        from jobbuddy.models import Company
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="m")
        # Re-save the company without bios — bios must survive (research owns them).
        store.save_company(Company(slug="acme", name="Acme Corp", ats="greenhouse", board="acme"))
        row = store.conn.execute(
            "SELECT short_bio, long_bio FROM companies WHERE slug = 'acme'"
        ).fetchone()
        assert row["short_bio"] == "s"
        assert row["long_bio"] == "l"

    def test_clear_company_bios_scoped(self, store):
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="m")
        store.update_company_bio("beta", short_bio="s", long_bio="l", model="m")

        cleared = store.clear_company_bios(slugs=["acme"])
        assert cleared == 1

        row = store.conn.execute(
            "SELECT short_bio, long_bio, bio_model, bio_researched_at"
            " FROM companies WHERE slug = 'acme'"
        ).fetchone()
        assert row["short_bio"] is None
        assert row["long_bio"] is None
        assert row["bio_model"] is None
        assert row["bio_researched_at"] is None

        # beta untouched
        beta = store.conn.execute(
            "SELECT long_bio FROM companies WHERE slug = 'beta'"
        ).fetchone()
        assert beta["long_bio"] == "l"

    def test_clear_company_bios_global(self, store):
        store.update_company_bio("acme", short_bio="s", long_bio="l", model="m")
        store.update_company_bio("beta", short_bio="s", long_bio="l", model="m")
        cleared = store.clear_company_bios()
        # Touches every row regardless of bio state.
        assert cleared == self._seeded_count(store)
        n_with_bio = store.conn.execute(
            "SELECT COUNT(*) AS c FROM companies WHERE long_bio IS NOT NULL"
        ).fetchone()["c"]
        assert n_with_bio == 0

    def test_hydrate_company_round_trips_bios(self, store):
        store.update_company_bio(
            "acme", short_bio="short text", long_bio="long text", model="gpt-5.4"
        )
        company = store.get_company("acme")
        assert company is not None
        assert company.short_bio == "short text"
        assert company.long_bio == "long text"


class TestNulByteSanitization:
    """Postgres text columns reject NUL (0x00). Real-world text reaching the
    store carries them anyway — the 2026-07-10 sync outage was triggered by a
    distill LLM emitting \\u0000 in its JSON output, which killed the
    WriteQueue mid-run. The store is the last gate before Postgres: every
    text write path must strip NULs rather than crash.
    """

    def test_update_job_distill_strips_nul(self, store):
        store.upsert_jobs("acme", [make_job("1", description="raw jd")])
        pk = store.conn.execute(
            "SELECT id FROM jobs WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()["id"]

        store.update_job_distill(
            pk,
            short_jd="short\x00jd",
            description_normalized="normal\x00ized",
            salary="$100k\x00-$150k",
        )
        row = store.conn.execute(
            "SELECT short_jd, description_normalized, salary FROM jobs WHERE id = %s",
            (pk,),
        ).fetchone()
        assert row["short_jd"] == "shortjd"
        assert row["description_normalized"] == "normalized"
        assert row["salary"] == "$100k-$150k"

    def test_upsert_jobs_strips_nul_from_text_fields(self, store):
        store.upsert_jobs(
            "acme",
            [make_job("1", title="Eng\x00ineer", description="desc\x00ription",
                      salary="$1\x000")],
        )
        row = store.conn.execute(
            "SELECT title, description, salary FROM jobs"
            " WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["title"] == "Engineer"
        assert row["description"] == "description"
        assert row["salary"] == "$10"

    def test_update_descriptions_strips_nul(self, store):
        store.upsert_jobs("acme", [make_job("1")])
        store.update_descriptions("acme", {"1": "enriched\x00 body"})
        row = store.conn.execute(
            "SELECT description FROM jobs WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["description"] == "enriched body"

    def test_update_enrichment_strips_nul(self, store):
        store.upsert_jobs("acme", [make_job("1")])
        store.update_enrichment("acme", {"1": {"description": "fill\x00ed"}})
        row = store.conn.execute(
            "SELECT description FROM jobs WHERE company_slug = 'acme' AND job_id = '1'"
        ).fetchone()
        assert row["description"] == "filled"
