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

    def test_posted_after_uses_freshness_when_present(self, store):
        """A job whose `published_at` is years stale but whose
        `last_listing_update` is recent IS in-window for posted_after."""
        today = date.today()
        old_pub = (today - timedelta(days=400)).isoformat()
        recent = (today - timedelta(days=2)).isoformat()
        _seed_distilled(store, "acme", "stale", title="Stale", short_jd="x",
                        published_at=old_pub)
        store.upsert_jobs("acme", [
            make_job(id="refreshed", title="Refreshed",
                     published_at=old_pub, last_listing_update=recent),
        ])
        cutoff = (today - timedelta(days=7)).isoformat()
        rows = store.search_jobs_fts(posted_after=cutoff, limit=20)
        assert [r["job_id"] for r in rows] == ["refreshed"]

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

    def test_no_per_company_cap_returns_all_matches(self, store):
        # The old PER_COMPANY_CAP_DEFAULT silently hid rows. Now the flat
        # search returns everything in rank order — callers that want
        # per-company breakdowns use survey_jobs_by_company.
        today = date.today()
        for i in range(5):
            _seed_distilled(store, "acme", f"a{i}", title=f"A{i}", short_jd="x",
                            published_at=today - timedelta(days=i))
        _seed_distilled(store, "beta", "b1", title="B1", short_jd="x",
                        published_at=today - timedelta(days=10))
        rows = store.search_jobs_fts(limit=10)
        slugs = [r["company_slug"] for r in rows]
        assert slugs.count("acme") == 5
        assert slugs.count("beta") == 1

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


class TestSurveyJobsByCompany:
    def test_returns_envelope_keyed_by_slug(self, store):
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        _seed_distilled(store, "beta", "b1", title="B1", short_jd="x")
        env = store.survey_jobs_by_company(companies=["acme", "beta"])
        assert set(env.keys()) == {"acme", "beta"}
        assert env["acme"]["matches"] == 1
        assert env["beta"]["matches"] == 1
        assert len(env["acme"]["top"]) == 1
        assert env["acme"]["top"][0]["job_id"] == "a1"
        assert env["acme"]["top"][0]["company_slug"] == "acme"
        assert env["acme"]["top"][0]["company_name"] == "Acme Corp"

    def test_zero_match_company_is_first_class(self, store):
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        env = store.survey_jobs_by_company(companies=["acme", "beta"])
        assert env["beta"] == {"matches": 0, "top": []}

    def test_matches_without_date_only_when_posted_after_set(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "old", title="Old", short_jd="x",
                        published_at=today - timedelta(days=30))
        _seed_distilled(store, "acme", "new", title="New", short_jd="x",
                        published_at=today - timedelta(days=2))
        # Without posted_after — no matches_without_date key.
        env = store.survey_jobs_by_company(companies=["acme"])
        assert "matches_without_date" not in env["acme"]
        # With posted_after — present and counts the wider window.
        cutoff = (today - timedelta(days=7)).isoformat()
        env = store.survey_jobs_by_company(companies=["acme"], posted_after=cutoff)
        assert env["acme"]["matches"] == 1
        assert env["acme"]["matches_without_date"] == 2

    def test_top_includes_snippet_when_query_present(self, store):
        _seed_distilled(store, "acme", "1",
                        title="Senior Engineer",
                        short_jd="Operate Kubernetes clusters at scale across many production fleets daily.")
        env = store.survey_jobs_by_company(companies=["acme"], query="kubernetes")
        top = env["acme"]["top"]
        assert len(top) == 1
        assert "snippet" in top[0]
        assert "**" in top[0]["snippet"]  # ts_headline bold markers

    def test_top_omits_snippet_field_format_when_no_query(self, store):
        # No query → snippet falls back to the first ~25 words of short_jd
        # (no ts_headline bold markers).
        _seed_distilled(store, "acme", "1", title="A",
                        short_jd="One two three four five six seven eight nine ten.")
        env = store.survey_jobs_by_company(companies=["acme"])
        snippet = env["acme"]["top"][0].get("snippet")
        assert snippet is not None
        assert "**" not in snippet

    def test_top_orders_by_ts_rank_when_query_present(self, store):
        _seed_distilled(store, "acme", "title-hit",
                        title="Senior Rust Engineer",
                        short_jd="Backend services.")
        _seed_distilled(store, "acme", "body-hit",
                        title="Senior Backend Engineer",
                        short_jd="Build services in Rust.")
        env = store.survey_jobs_by_company(companies=["acme"], query="rust")
        ids = [r["job_id"] for r in env["acme"]["top"]]
        assert ids[0] == "title-hit"

    def test_top_orders_by_published_at_when_no_query(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "old", title="Old", short_jd="x",
                        published_at=today - timedelta(days=30))
        _seed_distilled(store, "acme", "new", title="New", short_jd="x",
                        published_at=today - timedelta(days=1))
        env = store.survey_jobs_by_company(companies=["acme"])
        ids = [r["job_id"] for r in env["acme"]["top"]]
        assert ids == ["new", "old"]

    def test_top_per_company_limits_rows(self, store):
        for i in range(5):
            _seed_distilled(store, "acme", f"a{i}", title=f"A{i}", short_jd="x")
        env = store.survey_jobs_by_company(companies=["acme"], top_per_company=2)
        assert env["acme"]["matches"] == 5
        assert len(env["acme"]["top"]) == 2

    def test_top_omits_snippet_when_short_jd_null(self, store):
        from conftest import make_job
        store.upsert_jobs("acme", [make_job(id="raw", title="Plumber", description="raw body")])
        env = store.survey_jobs_by_company(companies=["acme"], query="plumber")
        top = env["acme"]["top"]
        assert len(top) == 1
        assert "snippet" not in top[0]


class TestPublishedAfter:
    """published_since (store-level: published_after) is the strict
    original-publish floor — bypasses ATS update-bumps that posted_since
    follows. Use when 'fresh' must mean 'newly created.'
    """

    def test_published_after_filters_old_published_even_if_recently_updated(self, store):
        today = date.today()
        old_pub = (today - timedelta(days=400))
        recent = (today - timedelta(days=2))
        # Stale-but-touched: published 400d ago, ATS touched it 2d ago.
        store.upsert_jobs("acme", [
            make_job(id="stale-touched", title="Stale-touched",
                     published_at=old_pub, last_listing_update=recent),
            make_job(id="genuinely-new", title="Genuinely new",
                     published_at=today - timedelta(days=3)),
        ])
        cutoff = (today - timedelta(days=7)).isoformat()
        # posted_after lets the touched-listing through (intended).
        rows = store.search_jobs_fts(posted_after=cutoff, limit=20)
        ids_posted = {r["job_id"] for r in rows}
        assert ids_posted == {"stale-touched", "genuinely-new"}
        # published_after rejects the touched-but-old listing.
        rows = store.search_jobs_fts(published_after=cutoff, limit=20)
        ids_pub = {r["job_id"] for r in rows}
        assert ids_pub == {"genuinely-new"}

    def test_posted_and_published_AND_together(self, store):
        today = date.today()
        store.upsert_jobs("acme", [
            make_job(id="new", title="new",
                     published_at=today - timedelta(days=2),
                     last_listing_update=today - timedelta(days=1)),
            make_job(id="old", title="old",
                     published_at=today - timedelta(days=100),
                     last_listing_update=today - timedelta(days=1)),
        ])
        cutoff = (today - timedelta(days=7)).isoformat()
        rows = store.search_jobs_fts(
            posted_after=cutoff, published_after=cutoff, limit=20,
        )
        assert {r["job_id"] for r in rows} == {"new"}


class TestFreshnessBucketOrdering:
    """Freshness bucket on `published_at` leads ORDER BY so stale-but-touched
    listings stop bleeding into the tail of result sets. Buckets:
    0=<=7d, 1=<=30d, 2=<=90d, 3=older or null.
    """

    def test_freshness_bucket_pushes_old_published_below_new(self, store):
        today = date.today()
        # Old publish, very recent effective_date (recently touched).
        store.upsert_jobs("acme", [
            make_job(id="stale-touched", title="rust role",
                     published_at=today - timedelta(days=400),
                     last_listing_update=today),
        ])
        # Genuinely new, posted 5 days ago.
        _seed_distilled(store, "beta", "fresh", title="rust role", short_jd="x",
                        published_at=today - timedelta(days=5))
        rows = store.search_jobs_fts(query="rust", limit=10)
        ids = [r["job_id"] for r in rows]
        # Fresh row (bucket 0) wins over stale-but-touched (bucket 3),
        # even though stale-touched has a more recent effective_date.
        assert ids.index("fresh") < ids.index("stale-touched")

    def test_freshness_bucket_in_empty_query_path(self, store):
        today = date.today()
        store.upsert_jobs("acme", [
            make_job(id="stale-touched", title="A",
                     published_at=today - timedelta(days=400),
                     last_listing_update=today),
        ])
        _seed_distilled(store, "beta", "fresh", title="B", short_jd="x",
                        published_at=today - timedelta(days=5))
        rows = store.search_jobs_fts(limit=10)
        ids = [r["job_id"] for r in rows]
        assert ids.index("fresh") < ids.index("stale-touched")

    def test_freshness_bucket_in_survey(self, store):
        today = date.today()
        # acme: one stale-touched, one fresh.
        store.upsert_jobs("acme", [
            make_job(id="stale-touched", title="A",
                     published_at=today - timedelta(days=400),
                     last_listing_update=today),
            make_job(id="fresh", title="B",
                     published_at=today - timedelta(days=3)),
        ])
        env = store.survey_jobs_by_company(companies=["acme"], top_per_company=10)
        ids = [r["job_id"] for r in env["acme"]["top"]]
        assert ids.index("fresh") < ids.index("stale-touched")


class TestTitleMatchTier:
    """Rows whose *title* matches the query outrank rows that only match in
    the body — across freshness buckets. Searching "product manager" must
    surface Product Manager roles before fresh postings that merely mention
    product managers in the JD. Within a tier, the freshness bucket and
    ts_rank still apply.
    """

    def test_title_match_beats_fresher_body_mention(self, store):
        today = date.today()
        # Published 20 days ago (bucket 1), but the title IS the query.
        _seed_distilled(store, "acme", "real-pm",
                        title="Senior Product Manager",
                        short_jd="Own the roadmap.",
                        published_at=today - timedelta(days=20))
        # Published today (bucket 0), query terms only in the body.
        _seed_distilled(store, "beta", "ux-mention",
                        title="UX Designer",
                        short_jd="Partner with product managers on discovery.",
                        published_at=today)
        rows = store.search_jobs_fts(query="product manager", limit=10)
        ids = [r["job_id"] for r in rows]
        assert ids.index("real-pm") < ids.index("ux-mention")

    def test_quoted_phrase_title_match_beats_fresher_body_mention(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "real-pm",
                        title="Senior Product Manager",
                        short_jd="Own the roadmap.",
                        published_at=today - timedelta(days=20))
        _seed_distilled(store, "beta", "ux-mention",
                        title="UX Designer",
                        short_jd="Partner with product managers on discovery.",
                        published_at=today)
        rows = store.search_jobs_fts(query='"product manager"', limit=10)
        ids = [r["job_id"] for r in rows]
        assert ids.index("real-pm") < ids.index("ux-mention")

    def test_freshness_bucket_still_orders_within_title_tier(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "pm-fresh",
                        title="Product Manager, Payments", short_jd="x",
                        published_at=today - timedelta(days=2))
        _seed_distilled(store, "beta", "pm-older",
                        title="Product Manager, Risk", short_jd="x",
                        published_at=today - timedelta(days=20))
        rows = store.search_jobs_fts(query="product manager", limit=10)
        ids = [r["job_id"] for r in rows]
        assert ids.index("pm-fresh") < ids.index("pm-older")

    def test_title_match_tier_in_survey_top(self, store):
        today = date.today()
        _seed_distilled(store, "acme", "real-pm",
                        title="Senior Product Manager",
                        short_jd="Own the roadmap.",
                        published_at=today - timedelta(days=20))
        _seed_distilled(store, "acme", "ux-mention",
                        title="UX Designer",
                        short_jd="Partner with product managers on discovery.",
                        published_at=today)
        env = store.survey_jobs_by_company(
            companies=["acme"], query="product manager", top_per_company=10)
        ids = [r["job_id"] for r in env["acme"]["top"]]
        assert ids.index("real-pm") < ids.index("ux-mention")


class TestCoreSearchJobs:
    def test_invalid_posted_since_raises_value_error(self, store):
        from jobbuddy.core import search_jobs
        with pytest.raises(ValueError, match="Invalid duration"):
            search_jobs(posted_since="garbage")

    def test_unknown_excluded_company_raises_value_error(self, store):
        from jobbuddy.core import search_jobs
        with pytest.raises(ValueError, match="Unknown company"):
            search_jobs(exclude_companies=["nonexistent-co"])

    def test_core_search_jobs_returns_flat_list(self, store):
        from jobbuddy.core import JobRow, search_jobs
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        result = search_jobs()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], JobRow)
        assert result[0].job_id == "a1"
        assert result[0].company_slug == "acme"

    def test_core_search_jobs_published_since(self, store):
        """published_since hits the strict-publish predicate end-to-end."""
        from jobbuddy.core import search_jobs

        today = date.today()
        store.upsert_jobs("acme", [
            make_job(id="stale-touched", title="r",
                     published_at=today - timedelta(days=400),
                     last_listing_update=today),
            make_job(id="genuinely-new", title="r",
                     published_at=today - timedelta(days=2)),
        ])
        rows = search_jobs(published_since="1w", limit=20)
        assert {r.job_id for r in rows} == {"genuinely-new"}

    def test_invalid_published_since_raises(self, store):
        from jobbuddy.core import search_jobs
        with pytest.raises(ValueError, match="Invalid duration"):
            search_jobs(published_since="garbage")


class TestCoreSurveyJobsByCompanies:
    def test_core_survey_returns_envelope(self, store):
        from jobbuddy.core import CompanyEnvelope, JobRow, survey_jobs_by_companies
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        _seed_distilled(store, "beta", "b1", title="B1", short_jd="x")
        result = survey_jobs_by_companies(companies=["Acme Corp", "Beta Inc"])
        assert isinstance(result, dict)
        assert set(result.keys()) == {"acme", "beta"}
        env = result["acme"]
        assert isinstance(env, CompanyEnvelope)
        assert env.matches == 1
        assert len(env.top) == 1
        assert isinstance(env.top[0], JobRow)
        assert env.top[0].job_id == "a1"

    def test_core_survey_single_company(self, store):
        from jobbuddy.core import survey_jobs_by_companies
        _seed_distilled(store, "acme", "a1", title="A1", short_jd="x")
        result = survey_jobs_by_companies(companies=["acme"])
        assert list(result.keys()) == ["acme"]

    def test_core_survey_requires_companies(self, store):
        from jobbuddy.core import survey_jobs_by_companies
        with pytest.raises(ValueError, match="non-empty"):
            survey_jobs_by_companies(companies=[])

    def test_core_survey_unknown_company_raises(self, store):
        from jobbuddy.core import survey_jobs_by_companies
        with pytest.raises(ValueError, match="Unknown company"):
            survey_jobs_by_companies(companies=["nonexistent-co"])


# ---------------------------------------------------------------------------
# ATS filter — case/punctuation-insensitive narrowing by ATS platform
# ---------------------------------------------------------------------------


def _seed_ats_company(store, slug: str, *, name: str, ats: str) -> None:
    """Add or update a test company with a specific `ats` value.

    The session-level test_companies fixture seeds greenhouse + workday.
    Tests that need a third ATS (e.g. compound `oracle_hcm`) use this
    helper to add it for the duration of the test (companies persist by
    design — the per-test cleanup only nulls bio fields)."""
    from jobbuddy.models import Company
    store.save_company(Company(slug=slug, name=name, ats=ats, board=slug))


class TestNormalizeAtsFilter:
    """The shared normalizer lowercases, strips whitespace, and drops
    non-alphanumeric characters. `greenhouse` / `Greenhouse` / `green-house`
    / `GREEN HOUSE` all collapse to `greenhouse`. Compound DB values like
    `oracle_hcm` collapse to `oraclehcm` so user inputs like `oracle-hcm`,
    `Oracle HCM`, `oracle_hcm` all match the same canonical key."""

    def test_basic_lowercase_strip(self):
        from jobbuddy.core.search import normalize_ats_filter
        assert normalize_ats_filter(["Greenhouse"]) == ["greenhouse"]
        assert normalize_ats_filter([" greenhouse "]) == ["greenhouse"]

    def test_punctuation_stripped(self):
        from jobbuddy.core.search import normalize_ats_filter
        assert normalize_ats_filter(["green-house", "GREEN HOUSE"]) == [
            "greenhouse", "greenhouse",
        ]

    def test_underscore_compound_collapses(self):
        from jobbuddy.core.search import normalize_ats_filter
        # All three user inputs collapse to the same key, which is also
        # how `oracle_hcm` collapses DB-side.
        assert normalize_ats_filter(["oracle_hcm"]) == ["oraclehcm"]
        assert normalize_ats_filter(["oracle-hcm"]) == ["oraclehcm"]
        assert normalize_ats_filter(["Oracle HCM"]) == ["oraclehcm"]

    def test_empty_and_none(self):
        from jobbuddy.core.search import normalize_ats_filter
        assert normalize_ats_filter(None) == []
        assert normalize_ats_filter([]) == []
        assert normalize_ats_filter(["", "  ", "!!"]) == []


class TestSearchJobsAtsFilter:
    """Core search_jobs accepts an `ats` list and filters by
    companies.ats. Test fixtures seed greenhouse (acme, beta, good, bad)
    and workday (workday-co)."""

    def test_baseline_no_filter_returns_multiple_atses(self, store):
        from jobbuddy.core import search_jobs

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        rows = search_jobs(limit=20)
        slugs = {r.company_slug for r in rows}
        assert "acme" in slugs
        assert "workday-co" in slugs

    def test_filter_to_greenhouse(self, store):
        from jobbuddy.core import search_jobs

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        rows = search_jobs(ats=["greenhouse"], limit=20)
        slugs = {r.company_slug for r in rows}
        assert slugs == {"acme"}

    def test_filter_to_two_atses(self, store):
        from jobbuddy.core import search_jobs

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        rows = search_jobs(ats=["greenhouse", "workday"], limit=20)
        slugs = {r.company_slug for r in rows}
        assert slugs == {"acme", "workday-co"}

    def test_liberal_normalization(self, store):
        from jobbuddy.core import search_jobs

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        rows = search_jobs(ats=["Greenhouse, ", "WORKDAY"], limit=20)
        slugs = {r.company_slug for r in rows}
        assert slugs == {"acme", "workday-co"}

    def test_unknown_ats_silently_drops(self, store):
        from jobbuddy.core import search_jobs

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        rows = search_jobs(ats=["nonexistent"], limit=20)
        assert rows == []

    def test_compound_ats_underscore(self, store):
        from jobbuddy.core import search_jobs

        _seed_ats_company(store, "oracle-co", name="Oracle Co", ats="oracle_hcm")
        _seed_distilled(store, "oracle-co", "o1", title="O", short_jd="x")
        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")

        # All three input forms route to the `oracle_hcm` DB value.
        for variant in (["oracle_hcm"], ["oracle-hcm"], ["Oracle HCM"]):
            rows = search_jobs(ats=variant, limit=20)
            slugs = {r.company_slug for r in rows}
            assert slugs == {"oracle-co"}, f"variant {variant!r} got {slugs}"


class TestSurveyJobsByCompaniesAtsFilter:
    """survey_jobs_by_companies filters its company set by ATS too —
    a company in the caller's `companies` list whose ATS is filtered out
    is omitted from the envelope."""

    def test_filter_narrows_envelope(self, store):
        from jobbuddy.core import survey_jobs_by_companies

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        env = survey_jobs_by_companies(
            companies=["acme", "workday-co"], ats=["greenhouse"],
        )
        assert set(env.keys()) == {"acme"}
        assert env["acme"].matches == 1

    def test_baseline_no_filter(self, store):
        from jobbuddy.core import survey_jobs_by_companies

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        env = survey_jobs_by_companies(companies=["acme", "workday-co"])
        assert set(env.keys()) == {"acme", "workday-co"}

    def test_two_atses(self, store):
        from jobbuddy.core import survey_jobs_by_companies

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        env = survey_jobs_by_companies(
            companies=["acme", "workday-co"], ats=["greenhouse", "ashby"],
        )
        # ashby has no test companies but greenhouse matches acme.
        assert set(env.keys()) == {"acme"}

    def test_liberal_normalization(self, store):
        from jobbuddy.core import survey_jobs_by_companies

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        _seed_distilled(store, "workday-co", "w1", title="W", short_jd="x")
        env = survey_jobs_by_companies(
            companies=["acme", "workday-co"], ats=["Greenhouse, ", "WORKDAY"],
        )
        assert set(env.keys()) == {"acme", "workday-co"}

    def test_unknown_ats_returns_empty(self, store):
        from jobbuddy.core import survey_jobs_by_companies

        _seed_distilled(store, "acme", "a1", title="A", short_jd="x")
        env = survey_jobs_by_companies(companies=["acme"], ats=["nonexistent"])
        assert env == {}
