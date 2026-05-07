"""Tests for the Account UPSERT path and provider-specific external_id pick.

Account rows appear lazily on the first authenticated MCP request — there
is no signup form. The UPSERT must be idempotent across token refreshes
and must refresh denormalized snapshot fields (email/display_name/handle)
so the DB always reflects the latest provider-side state.
"""

import pytest

from jobbuddy.store import JobStore, _pick_external_id


class TestUpsertAccountFromClaims:
    def test_creates_new_account(self, store: JobStore):
        account = store.upsert_account_from_claims(
            "entra",
            {
                "oid": "00000000-0000-0000-0000-000000000abc",
                "sub": "ent-sub-1",
                "email": "alice@example.com",
                "name": "Alice",
                "preferred_username": "alice@example.com",
            },
        )
        assert account.provider == "entra"
        assert account.external_id == "00000000-0000-0000-0000-000000000abc"
        assert account.email == "alice@example.com"
        assert account.display_name == "Alice"
        assert account.handle == "alice@example.com"

    def test_idempotent_second_call_returns_same_id(self, store: JobStore):
        first = store.upsert_account_from_claims(
            "entra", {"oid": "stable-oid", "name": "Initial"},
        )
        second = store.upsert_account_from_claims(
            "entra", {"oid": "stable-oid", "name": "Renamed"},
        )
        assert first.id == second.id
        # Snapshot fields refresh on each call.
        assert second.display_name == "Renamed"

    def test_different_providers_with_same_external_id_are_distinct(self, store: JobStore):
        a = store.upsert_account_from_claims("entra", {"oid": "shared-id"})
        b = store.upsert_account_from_claims("github", {"sub": "shared-id"})
        assert a.id != b.id

    def test_github_uses_sub(self, store: JobStore):
        account = store.upsert_account_from_claims(
            "github",
            {"sub": "12345678", "login": "octocat", "name": "Octo Cat", "email": None},
        )
        assert account.external_id == "12345678"
        assert account.handle == "octocat"

    def test_entra_prefers_oid_over_sub(self, store: JobStore):
        """`oid` is per-tenant-stable; `sub` is per-(user, app) and would
        change if the upstream app registration is replaced. We must pick
        oid when both are present."""
        account = store.upsert_account_from_claims(
            "entra",
            {"oid": "oid-value", "sub": "sub-value"},
        )
        assert account.external_id == "oid-value"

    def test_entra_falls_back_to_sub_when_oid_absent(self, store: JobStore):
        account = store.upsert_account_from_claims(
            "entra",
            {"sub": "sub-only"},
        )
        assert account.external_id == "sub-only"

    def test_entra_reads_upstream_claims_subdict(self, store: JobStore):
        """FastMCP's OAuthProxy embeds a filtered claim subset under
        `claims['upstream_claims']` (proxy.py:1712). Our resolver must
        treat that as a fallback so callers handed only the subset still
        resolve."""
        account = store.upsert_account_from_claims(
            "entra",
            {"upstream_claims": {"oid": "from-upstream"}},
        )
        assert account.external_id == "from-upstream"

    def test_missing_external_id_raises(self, store: JobStore):
        with pytest.raises(ValueError, match="external_id"):
            store.upsert_account_from_claims("entra", {"name": "no ids here"})

    def test_get_account_round_trip(self, store: JobStore):
        created = store.upsert_account_from_claims(
            "entra", {"oid": "round-trip-oid", "name": "Cory"},
        )
        fetched = store.get_account(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.display_name == "Cory"


class TestPickExternalId:
    def test_unknown_provider_falls_back_to_sub(self):
        assert _pick_external_id("custom", {"sub": "x"}) == "x"

    def test_returns_none_when_nothing_present(self):
        assert _pick_external_id("entra", {}) is None
