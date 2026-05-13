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
    def test_explicit_args_win(self):
        wl = {
            "filter": {"query": "rust", "posted_since": "7d"},
            "companies": ["acme"],
        }
        q, ex, loc, since, comp = merge_watchlist_defaults(
            wl, query="python", exclude_companies=None,
            location="", posted_since="",
        )
        assert q == "python"  # caller overrode
        assert since == "7d"  # watchlist filled it in
        assert comp == ["acme"]

    def test_caller_companies_override_watchlist(self):
        wl = {"filter": {}, "companies": ["acme"]}
        _, _, _, _, comp = merge_watchlist_defaults(
            wl, query="", exclude_companies=None,
            location="", posted_since="",
            companies=["beta"],
        )
        assert comp == ["beta"]

    def test_rejects_unknown_filter_keys(self):
        wl = {"filter": {"bogus": 1}, "companies": []}
        with pytest.raises(ValueError, match="unknown keys"):
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
        q, ex, loc, since, comp = merge_watchlist_defaults(
            wl, query="", exclude_companies=None, location="", posted_since="",
        )
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since, limit=20)
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
        q, ex, loc, since, comp = merge_watchlist_defaults(
            wl, query="", exclude_companies=None, location="", posted_since="",
        )
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since, limit=20)
        ids = {r.job_id for r in rows}
        assert "match" in ids
        assert "miss" not in ids

    def test_explicit_query_overrides_saved(self, store: JobStore, test_account: Account):
        from jobbuddy.core import merge_watchlist_defaults, search_jobs

        today = date.today()
        store.upsert_jobs("acme", [
            make_job(id="rust", title="rust engineer",
                     published_at=today, description="rust"),
            make_job(id="python", title="python engineer",
                     published_at=today, description="python"),
        ])
        store.create_watchlist(
            test_account.id, slug="se", name="se",
            company_slugs=["acme"],
            filter={"query": "rust"},
        )
        wl = store.get_watchlist(test_account.id, "se")
        q, ex, loc, since, comp = merge_watchlist_defaults(
            wl, query="python",  # caller override
            exclude_companies=None, location="", posted_since="",
        )
        assert q == "python"
        rows = search_jobs(query=q, companies=comp, exclude_companies=ex,
                           location=loc, posted_since=since, limit=20)
        ids = {r.job_id for r in rows}
        assert "python" in ids
        assert "rust" not in ids
