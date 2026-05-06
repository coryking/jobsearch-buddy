"""Tests for the search surface.

Covers JobStore.search_jobs_fts and core.search_jobs — the path that
both `jsb search` and the `search_jobs` MCP tool sit on top of.
"""

from datetime import date, timedelta

import pytest

from jobbuddy.models import Job

from conftest import make_job


def _seed_distilled(store, slug: str, job_id: str, *, title: str, short_jd: str,
                    description_normalized: str | None = None,
                    salary: str | None = None,
                    published_at: date | None = None,
                    location: str = "Remote") -> None:
    """Seed a single distilled job without triggering the soft-delete behavior
    of upsert_jobs (which marks any existing rows for this slug not in the
    incoming list as removed). Tests need to seed multiple rows incrementally.
    """
    store.conn.execute(
        """INSERT INTO jobs (company_slug, job_id, title, location, url,
                             published_at, salary, description, short_jd,
                             description_normalized, last_seen, listing_status)
           VALUES (%s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s, %s, %s, %s, now(), 'active')""",
        (slug, job_id, title, location, f"https://example.com/{job_id}",
         published_at, salary, "raw body", short_jd,
         description_normalized or short_jd),
    )


class TestSearchJobsFts:
    def test_returns_short_jd_inline(self, store):
        _seed_distilled(store, "acme", "1",
                        title="Backend Engineer",
                        short_jd="Build internal APIs in Go.",
                        salary="$180k–$220k")
        rows = store.search_jobs_fts(limit=20)
        assert len(rows) == 1
        r = rows[0]
        assert r["short_jd"] == "Build internal APIs in Go."
        assert r["salary"] == "$180k–$220k"
        assert r["company_slug"] == "acme"
        assert r["company_name"] == "Acme Corp"

    def test_empty_query_orders_by_published_desc(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "old", title="Old", short_jd="x",
                        published_at=today - timedelta(days=30))
        _seed_distilled(store, "acme", "new", title="New", short_jd="x",
                        published_at=today - timedelta(days=1))
        _seed_distilled(store, "acme", "mid", title="Mid", short_jd="x",
                        published_at=today - timedelta(days=10))
        rows = store.search_jobs_fts(limit=20)
        assert [r["job_id"] for r in rows] == ["new", "mid", "old"]

    def test_query_uses_ts_rank(self, store):
        # "rust" appears in title (weight A) for one row, only in short_jd
        # (weight B) for another. Title hit should rank higher.
        _seed_distilled(store, "acme", "title-hit",
                        title="Senior Rust Engineer",
                        short_jd="Backend services.")
        _seed_distilled(store, "acme", "body-hit",
                        title="Senior Backend Engineer",
                        short_jd="Build services in Rust.")
        rows = store.search_jobs_fts(query="rust", limit=20)
        ids = [r["job_id"] for r in rows]
        assert ids[0] == "title-hit"
        assert "body-hit" in ids

    def test_query_matches_short_jd_content(self, store):
        # Title has nothing about Kubernetes; short_jd does. Phase 1 promise:
        # search_jobs returns distilled jobs by content the calling LLM cares
        # about, not just title.
        _seed_distilled(store, "acme", "1",
                        title="Senior Engineer",
                        short_jd="Operate Kubernetes clusters at scale.")
        rows = store.search_jobs_fts(query="kubernetes", limit=20)
        assert [r["job_id"] for r in rows] == ["1"]

    def test_posted_after_filter(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "old", title="Old", short_jd="x",
                        published_at=today - timedelta(days=30))
        _seed_distilled(store, "acme", "new", title="New", short_jd="x",
                        published_at=today - timedelta(days=2))
        cutoff = (today - timedelta(days=7)).isoformat()
        rows = store.search_jobs_fts(posted_after=cutoff, limit=20)
        assert [r["job_id"] for r in rows] == ["new"]

    def test_excludes_removed_listings(self, store):
        _seed_distilled(store, "acme", "1", title="A", short_jd="x")
        _seed_distilled(store, "acme", "2", title="B", short_jd="x")
        # Mark "1" removed by re-syncing without it
        store.upsert_jobs("acme", [make_job(id="2", title="B")])
        rows = store.search_jobs_fts(limit=20)
        assert [r["job_id"] for r in rows] == ["2"]

    def test_company_filter(self, store):
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        _seed_distilled(store, "beta", "b1", title="B1", short_jd="x")
        rows = store.search_jobs_fts(companies=["acme"], limit=20)
        assert {r["company_slug"] for r in rows} == {"acme"}

    def test_exclude_companies(self, store):
        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "beta", "b1", title="B", short_jd="x")
        rows = store.search_jobs_fts(exclude_companies=["beta"], limit=20)
        assert {r["company_slug"] for r in rows} == {"acme"}

    def test_per_company_cap_caps_default(self, store):
        # Default cap of 3 prevents one employer from flooding the result set.
        # Without the cap the top-5 would be 5 acme rows; with it, beta breaks in.
        today = date.today()
        for i in range(5):
            _seed_distilled(store, "acme", f"a{i}", title=f"A{i}", short_jd="x",
                            published_at=today - timedelta(days=i))
        _seed_distilled(store, "beta", "b1", title="B1", short_jd="x",
                        published_at=today - timedelta(days=10))
        rows = store.search_jobs_fts(limit=5)
        slugs = [r["company_slug"] for r in rows]
        assert slugs.count("acme") == 3
        assert slugs.count("beta") == 1
        # Within acme, the 3 newest should win (a0, a1, a2 by recency).
        acme_ids = [r["job_id"] for r in rows if r["company_slug"] == "acme"]
        assert acme_ids == ["a0", "a1", "a2"]

    def test_per_company_cap_disabled_returns_all(self, store):
        # Caller can opt out by passing per_company_cap=None.
        today = date.today()
        for i in range(5):
            _seed_distilled(store, "acme", f"a{i}", title=f"A{i}", short_jd="x",
                            published_at=today - timedelta(days=i))
        rows = store.search_jobs_fts(limit=5, per_company_cap=None)
        assert len(rows) == 5

    def test_per_company_cap_skipped_when_single_company_filter(self, store):
        # Scoping to a single company means "I want depth here." Cap shouldn't
        # silently truncate that to 3.
        today = date.today()
        for i in range(5):
            _seed_distilled(store, "acme", f"a{i}", title=f"A{i}", short_jd="x",
                            published_at=today - timedelta(days=i))
        rows = store.search_jobs_fts(companies=["acme"], limit=10)
        assert len(rows) == 5

    def test_limit_cap(self, store):
        for i in range(10):
            _seed_distilled(store, "acme", str(i), title=f"T{i}", short_jd="x")
        rows = store.search_jobs_fts(limit=3)
        assert len(rows) == 3

    def test_location_substring_filter(self, store):
        _seed_distilled(store, "acme", "1", title="A", short_jd="x", location="Seattle, WA")
        _seed_distilled(store, "acme", "2", title="B", short_jd="x", location="Remote (US)")
        rows = store.search_jobs_fts(location="seattle", limit=20)
        assert [r["job_id"] for r in rows] == ["1"]

    def test_distilled_flag_round_trip(self, store):
        # Distilled row → CompactJob.distilled is True
        from jobbuddy.models import CompactJob
        _seed_distilled(store, "acme", "1", title="A", short_jd="x",
                        description_normalized="full body")
        rows = store.search_jobs_fts(limit=20)
        cj = CompactJob.from_db_row(rows[0], "Acme Corp")
        assert cj.distilled is True
        assert cj.description == "full body"

        # Raw-only row → distilled False, raw description served
        store.upsert_jobs("beta", [make_job(id="r", title="Raw", location="x",
                                            description="raw text")])
        beta_row = store.search_jobs_fts(companies=["beta"], limit=20)[0]
        cj2 = CompactJob.from_db_row(beta_row, "Beta Inc")
        assert cj2.distilled is False
        assert cj2.description == "raw text"

    def test_undistilled_jobs_excluded_by_query_but_not_by_listing(self, store):
        # short_jd IS NULL → won't match an FTS query that targets short_jd
        # content, but the row still exists for unfiltered listings.
        store.upsert_jobs("acme", [make_job(id="raw", title="Plumber", location="x",
                                            description="raw body about plumbing")])
        rows = store.search_jobs_fts(limit=20)
        assert [r["job_id"] for r in rows] == ["raw"]
        assert rows[0]["short_jd"] is None
        # Title-match still works (title is part of fts_vector)
        rows = store.search_jobs_fts(query="plumber", limit=20)
        assert [r["job_id"] for r in rows] == ["raw"]


class TestCoreSearchJobs:
    def test_unknown_company_raises_value_error(self, store):
        from jobbuddy.core import search_jobs
        with pytest.raises(ValueError, match="Unknown company"):
            search_jobs(companies=["nonexistent-co"])

    def test_invalid_posted_since_raises_value_error(self, store):
        from jobbuddy.core import search_jobs
        with pytest.raises(ValueError, match="Invalid duration"):
            search_jobs(posted_since="garbage")
