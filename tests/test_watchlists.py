"""Store-layer tests for watchlists.

Cross-account isolation is the load-bearing invariant — one account's
watchlists must never bleed into another's reads or counts.
"""

from datetime import date, timedelta

import pytest

from jobbuddy.core import merge_watchlist_defaults
from jobbuddy.models import Account
from jobbuddy.store import JobStore
from tests.conftest import make_job


def _other_account(store: JobStore) -> Account:
    return store.upsert_account_from_claims(
        "entra",
        {
            "oid": "00000000-0000-0000-0000-000000000002",
            "sub": "test-subject-2",
            "name": "Other User",
        },
    )


class TestCreateAndGet:
    def test_basic_create_returns_full_row(self, store: JobStore, test_account: Account):
        row = store.create_watchlist(
            test_account.id, slug="ai", name="AI focus",
            filter={"query": "staff engineer"},
            company_slugs=["acme", "beta"],
            notes="track this",
        )
        assert row["slug"] == "ai"
        assert row["name"] == "AI focus"
        assert row["notes"] == "track this"
        assert row["filter"] == {"query": "staff engineer"}
        assert set(row["companies"]) == {"acme", "beta"}

    def test_get_by_account_and_slug(self, store: JobStore, test_account: Account):
        store.create_watchlist(test_account.id, slug="ai", name="AI focus")
        wl = store.get_watchlist(test_account.id, "ai")
        assert wl is not None
        assert wl["slug"] == "ai"

    def test_get_returns_none_for_missing(self, store: JobStore, test_account: Account):
        assert store.get_watchlist(test_account.id, "nope") is None

    def test_unknown_company_slug_is_silently_dropped(self, store: JobStore, test_account: Account):
        row = store.create_watchlist(
            test_account.id, slug="x", name="x",
            company_slugs=["acme", "does-not-exist"],
        )
        assert row["companies"] == ["acme"]

    def test_duplicate_slug_per_account_raises(self, store: JobStore, test_account: Account):
        store.create_watchlist(test_account.id, slug="ai", name="AI")
        with pytest.raises(ValueError, match="already exists"):
            store.create_watchlist(test_account.id, slug="ai", name="AI v2")

    def test_same_slug_different_accounts_is_allowed(self, store: JobStore, test_account: Account):
        other = _other_account(store)
        store.create_watchlist(test_account.id, slug="ai", name="A")
        store.create_watchlist(other.id, slug="ai", name="B")
        a = store.get_watchlist(test_account.id, "ai")
        b = store.get_watchlist(other.id, "ai")
        assert a is not None and a["name"] == "A"
        assert b is not None and b["name"] == "B"


class TestUpdate:
    def test_update_metadata_only(self, store: JobStore, test_account: Account):
        store.create_watchlist(test_account.id, slug="ai", name="AI")
        row = store.update_watchlist(
            test_account.id, "ai", name="AI v2", notes="updated",
            filter={"query": "rust"},
        )
        assert row is not None
        assert row["name"] == "AI v2"
        assert row["notes"] == "updated"
        assert row["filter"] == {"query": "rust"}

    def test_add_and_remove_members(self, store: JobStore, test_account: Account):
        store.create_watchlist(
            test_account.id, slug="ai", name="AI",
            company_slugs=["acme", "beta"],
        )
        row = store.update_watchlist(
            test_account.id, "ai", add=["good"], remove=["acme"],
        )
        assert row is not None
        assert set(row["companies"]) == {"beta", "good"}

    def test_update_missing_returns_none(self, store: JobStore, test_account: Account):
        assert store.update_watchlist(test_account.id, "nope", name="x") is None

    def test_account_isolation_on_update(self, store: JobStore, test_account: Account):
        other = _other_account(store)
        store.create_watchlist(other.id, slug="ai", name="theirs")
        # test_account tries to update the other account's watchlist — should miss
        assert store.update_watchlist(test_account.id, "ai", name="hijack") is None
        wl = store.get_watchlist(other.id, "ai")
        assert wl is not None and wl["name"] == "theirs"


class TestDelete:
    def test_delete_returns_true(self, store: JobStore, test_account: Account):
        store.create_watchlist(test_account.id, slug="ai", name="AI")
        assert store.delete_watchlist(test_account.id, "ai") is True
        assert store.get_watchlist(test_account.id, "ai") is None

    def test_delete_missing_returns_false(self, store: JobStore, test_account: Account):
        assert store.delete_watchlist(test_account.id, "nope") is False

    def test_account_isolation_on_delete(self, store: JobStore, test_account: Account):
        other = _other_account(store)
        store.create_watchlist(other.id, slug="ai", name="theirs")
        assert store.delete_watchlist(test_account.id, "ai") is False
        assert store.get_watchlist(other.id, "ai") is not None


class TestListWithCounts:
    def test_empty_list_for_new_account(self, store: JobStore, test_account: Account):
        assert store.list_watchlists(test_account.id) == []

    def test_freshness_histogram(self, store: JobStore, test_account: Account, pg_conninfo):
        today = date.today()
        jobs = [
            make_job(id="r1", title="recent",
                     published_at=today,
                     last_listing_update=today),
            make_job(id="w1", title="this week",
                     published_at=today - timedelta(days=5),
                     last_listing_update=today - timedelta(days=5)),
            make_job(id="m1", title="this month",
                     published_at=today - timedelta(days=20),
                     last_listing_update=today - timedelta(days=20)),
            make_job(id="old", title="stale",
                     published_at=today - timedelta(days=90),
                     last_listing_update=today - timedelta(days=90)),
        ]
        store.upsert_jobs("acme", jobs)
        store.create_watchlist(
            test_account.id, slug="ai", name="AI",
            company_slugs=["acme"],
        )
        rows = store.list_watchlists(test_account.id)
        assert len(rows) == 1
        counts = rows[0]["counts"]
        assert counts["total_active"] == 4
        # posted_1d catches today's job; the precise count depends on
        # CURRENT_DATE math, but it must not exceed posted_7d.
        assert counts["posted_1d"] >= 1
        assert counts["posted_7d"] >= 2
        assert counts["posted_7d"] <= counts["posted_30d"]
        assert counts["posted_30d"] == 3

    def test_applied_count(self, store: JobStore, test_account: Account):
        store.create_watchlist(
            test_account.id, slug="ai", name="AI",
            company_slugs=["acme", "beta"],
        )
        # Two applications at acme (in-watchlist), one at workday-co (not).
        store.append_activity(test_account.id, "Acme Corp", "Eng", "Application")
        store.append_activity(test_account.id, "Acme Corp", "PM", "Application")
        store.append_activity(test_account.id, "Workday Co", "Eng", "Application")
        # Non-Application activity must not be counted.
        store.append_activity(test_account.id, "Acme Corp", "Eng", "Screen")
        rows = store.list_watchlists(test_account.id)
        assert rows[0]["counts"]["applied"] == 2

    def test_account_isolation_on_list(self, store: JobStore, test_account: Account):
        other = _other_account(store)
        store.create_watchlist(test_account.id, slug="mine", name="mine")
        store.create_watchlist(other.id, slug="theirs", name="theirs")
        mine = store.list_watchlists(test_account.id)
        theirs = store.list_watchlists(other.id)
        assert [r["slug"] for r in mine] == ["mine"]
        assert [r["slug"] for r in theirs] == ["theirs"]


class TestMergeDefaults:
    """The watchlist's *curation* is a view: caller args compose with it,
    narrowing not overriding — queries AND, companies intersect, exclusions
    union. The *recency window* (posted_since/published_since) is the
    exception: a saved value is a default the caller overrides freely,
    narrower OR wider.
    """

    def test_only_caller_args(self):
        wl = {"filter": {}, "companies": []}
        q, ex, loc, since, comp, pubsince = merge_watchlist_defaults(
            wl, query="python", exclude_companies=None,
            location="", posted_since="",
        )
        assert q == "python"
        assert comp is None  # no watchlist companies

    def test_only_watchlist_args(self):
        wl = {
            "filter": {"query": "rust", "posted_since": "7d"},
            "companies": ["acme"],
        }
        q, ex, loc, since, comp, pubsince = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
        )
        assert q == "rust"
        assert since == "7d"
        assert comp == ["acme"]

    def test_query_composes_with_AND(self):
        wl = {
            "filter": {"query": "engineer or pm"},
            "companies": ["acme"],
        }
        q, *_ = merge_watchlist_defaults(
            wl, query="rust", exclude_companies=None,
            location="", posted_since="",
        )
        # Both queries apply; FTS sees (watchlist) AND (caller).
        assert q == "(engineer or pm) AND (rust)"

    def test_caller_companies_intersect_watchlist(self):
        wl = {"filter": {}, "companies": ["acme", "beta", "good"]}
        _, _, _, _, comp, _ = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
            companies=["beta", "bad"],  # "bad" not in watchlist
        )
        assert comp == ["beta"]

    def test_empty_company_intersect_raises(self):
        from jobbuddy.core.search import EmptyCompanyIntersectError
        wl = {"filter": {}, "companies": ["acme"]}
        with pytest.raises(EmptyCompanyIntersectError):
            merge_watchlist_defaults(
                wl, query="", exclude_companies=None,
                location="", posted_since="",
                companies=["beta"],
            )

    def test_exclude_companies_union(self):
        wl = {
            "filter": {"exclude_companies": ["microsoft"]},
            "companies": [],
        }
        _, ex, _, _, _, _ = merge_watchlist_defaults(
            wl, query="", exclude_companies=["meta"],
            location="", posted_since="",
        )
        assert set(ex) == {"microsoft", "meta"}

    def test_posted_since_caller_overrides(self):
        wl = {
            "filter": {"posted_since": "30d"},
            "companies": [],
        }
        _, _, _, since, _, _ = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="1w",
        )
        # Caller narrows below the saved window → caller wins.
        assert since == "1w"

        _, _, _, since2, _, _ = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="90d",
        )
        # Caller widens past the saved window → caller still wins (the fix:
        # the saved window is a default, not a floor that clamps narrower).
        assert since2 == "90d"

    def test_published_since_caller_overrides(self):
        wl = {
            "filter": {"published_since": "30d"},
            "companies": [],
        }
        *_, pubsince = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
            published_since="1w",
        )
        assert pubsince == "1w"  # caller narrows → wins

        *_, pubsince2 = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
            published_since="90d",
        )
        assert pubsince2 == "90d"  # caller widens → still wins

    def test_recency_window_widens_past_saved_default(self):
        """Regression: a watchlist saved at 1w must still honor a caller's
        2w request. Previously stricter-wins clamped this back to 1w, which
        forced callers to bypass the watchlist or destructively edit it.
        """
        wl = {"filter": {"posted_since": "1w", "published_since": "1w"}, "companies": []}

        # Caller widens to 2w → 2w on both axes.
        _, _, _, since, _, pubsince = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="2w", published_since="2w",
        )
        assert since == "2w"
        assert pubsince == "2w"

        # Caller omits a window → the saved default still "shows new jobs".
        _, _, _, since2, _, pubsince2 = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="", published_since="",
        )
        assert since2 == "1w"
        assert pubsince2 == "1w"

    def test_location_AND_stacks(self):
        wl = {"filter": {"location_filter": "seattle,remote"}, "companies": []}
        _, _, loc, _, _, _ = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="us", posted_since="",
        )
        # AND-joined via the && sentinel; store splits and ANDs the groups.
        assert "&&" in loc
        assert "seattle,remote" in loc
        assert "us" in loc

    def test_rejects_unknown_filter_keys(self):
        wl = {"filter": {"bogus": 1}, "companies": []}
        with pytest.raises(ValueError, match="unknown keys"):
            merge_watchlist_defaults(
                wl, query="", exclude_companies=None,
                location="", posted_since="",
            )

    def test_published_since_is_allowed_filter_key(self):
        wl = {"filter": {"published_since": "1w"}, "companies": []}
        # Should not raise — published_since is in WATCHLIST_FILTER_KEYS.
        merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
        )


class TestSearchJobsWatchlistIntegration:
    """End-to-end through core.search_jobs: watchlist scopes companies and
    layers in saved filters, while explicit caller args win."""

    def test_scopes_to_watchlist_companies(self, store: JobStore, test_account: Account):
        from jobbuddy.core import search_jobs

        today = date.today()
        store.upsert_jobs("acme", [make_job(id="a1", title="acme role", published_at=today)])
        store.upsert_jobs("beta", [make_job(id="b1", title="beta role", published_at=today)])
        store.upsert_jobs("good", [make_job(id="g1", title="good role", published_at=today)])
        store.create_watchlist(
            test_account.id, slug="mine", name="mine",
            company_slugs=["acme", "beta"],
        )
        wl = store.get_watchlist(test_account.id, "mine")
        assert wl is not None

        from jobbuddy.core import merge_watchlist_defaults
        q, ex, loc, since, comp, pubsince = merge_watchlist_defaults(
            wl, query="", exclude_companies=None, location="", posted_since="",
        )
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since,
                           published_since=pubsince, limit=20)
        slugs = {r.company_slug for r in rows}
        assert slugs == {"acme", "beta"}  # `good` was not in the watchlist

    def test_saved_filter_applies(self, store: JobStore, test_account: Account):
        from jobbuddy.core import merge_watchlist_defaults, search_jobs

        today = date.today()
        store.upsert_jobs("acme", [
            make_job(id="match", title="staff engineer",
                     published_at=today, description="staff engineer role"),
            make_job(id="miss", title="janitor",
                     published_at=today, description="janitor"),
        ])
        store.create_watchlist(
            test_account.id, slug="se", name="se",
            company_slugs=["acme"],
            filter={"query": "engineer"},
        )
        wl = store.get_watchlist(test_account.id, "se")
        q, ex, loc, since, comp, pubsince = merge_watchlist_defaults(
            wl, query="", exclude_companies=None, location="", posted_since="",
        )
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since,
                           published_since=pubsince, limit=20)
        ids = {r.job_id for r in rows}
        assert "match" in ids
        assert "miss" not in ids

    def test_caller_query_composes_with_saved(self, store: JobStore, test_account: Account):
        """Caller query AND-stacks with watchlist saved query — both must match.
        Replaces the prior override-semantic test.
        """
        from jobbuddy.core import merge_watchlist_defaults, search_jobs

        today = date.today()
        store.upsert_jobs("acme", [
            # Matches both "engineer" (saved) and "rust" (caller).
            make_job(id="both", title="rust engineer",
                     published_at=today, description="rust engineer"),
            # Matches saved but not caller.
            make_job(id="engineer-only", title="python engineer",
                     published_at=today, description="python engineer"),
            # Matches caller but not saved.
            make_job(id="rust-only", title="rust developer",
                     published_at=today, description="rust developer"),
        ])
        store.create_watchlist(
            test_account.id, slug="se", name="se",
            company_slugs=["acme"],
            filter={"query": "engineer"},
        )
        wl = store.get_watchlist(test_account.id, "se")
        q, ex, loc, since, comp, pubsince = merge_watchlist_defaults(
            wl, query="rust",
            exclude_companies=None, location="", posted_since="",
        )
        assert q == "(engineer) AND (rust)"
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since,
                           published_since=pubsince, limit=20)
        ids = {r.job_id for r in rows}
        assert ids == {"both"}
