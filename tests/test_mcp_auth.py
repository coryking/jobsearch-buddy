"""Tests for the FastMCP auth glue: account resolver + cache."""

from unittest.mock import MagicMock

import pytest

from jobbuddy.mcp_auth import AccountCache, account_cache, resolve_account
from jobbuddy.models import Account
from jobbuddy.store import JobStore


@pytest.fixture(autouse=True)
def reset_account_cache():
    """The module-level cache is process-wide; reset between tests so
    each one gets a clean slate without leaking state across test runs."""
    account_cache.clear()
    yield
    account_cache.clear()


def make_token(claims: dict) -> MagicMock:
    """A stand-in for fastmcp.server.auth.AccessToken that exposes only
    the surface our resolver needs."""
    tok = MagicMock()
    tok.claims = claims
    tok.token = "synthetic-token-string"
    return tok


class TestAccountCache:
    def test_miss_when_empty(self):
        cache = AccountCache()
        assert cache.get("entra", "abc", "jti1") is None

    def test_hit_with_matching_jti(self):
        cache = AccountCache()
        account = Account(
            id="00000000-0000-0000-0000-000000000001",
            provider="entra",
            external_id="abc",
        )
        cache.put("entra", "abc", "jti1", account)
        assert cache.get("entra", "abc", "jti1") is account

    def test_miss_when_jti_changes(self):
        """A new token rotation must miss the cache so the snapshot fields
        get refreshed via UPSERT — that's the whole point of jti tracking."""
        cache = AccountCache()
        account = Account(
            id="00000000-0000-0000-0000-000000000001",
            provider="entra",
            external_id="abc",
        )
        cache.put("entra", "abc", "jti1", account)
        assert cache.get("entra", "abc", "jti2") is None

    def test_invalidate_drops_entry(self):
        cache = AccountCache()
        account = Account(
            id="00000000-0000-0000-0000-000000000001",
            provider="entra",
            external_id="abc",
        )
        cache.put("entra", "abc", "jti1", account)
        cache.invalidate("entra", "abc")
        assert cache.get("entra", "abc", "jti1") is None

    def test_isolated_by_provider(self):
        """Same external_id under two providers must produce two distinct
        cache entries (e.g., a future GitHub user happens to share a
        Postgres row id format with an Entra oid — should never conflict)."""
        cache = AccountCache()
        a = Account(id="00000000-0000-0000-0000-000000000001", provider="entra", external_id="x")
        b = Account(id="00000000-0000-0000-0000-000000000002", provider="github", external_id="x")
        cache.put("entra", "x", "jti1", a)
        cache.put("github", "x", "jti1", b)
        assert cache.get("entra", "x", "jti1") is a
        assert cache.get("github", "x", "jti1") is b


class TestResolveAccount:
    def test_first_call_writes_to_db_and_caches(self, store: JobStore, monkeypatch):
        monkeypatch.setattr("jobbuddy.mcp_auth.get_settings", lambda: MagicMock(auth_provider="entra"))
        token = make_token({"oid": "user-1", "name": "First", "jti": "jti-A"})

        account = resolve_account(token)

        assert account.external_id == "user-1"
        assert account.display_name == "First"
        # Cached: same (provider, external_id, jti) returns the same object.
        assert account_cache.get("entra", "user-1", "jti-A") is account

    def test_same_jti_skips_db(self, monkeypatch):
        """Once cached, a subsequent resolve with the same token must not
        round-trip to Postgres. We assert this by patching JobStore to
        explode on instantiation."""
        monkeypatch.setattr("jobbuddy.mcp_auth.get_settings", lambda: MagicMock(auth_provider="entra"))
        seeded = Account(
            id="00000000-0000-0000-0000-000000000001",
            provider="entra",
            external_id="user-1",
        )
        account_cache.put("entra", "user-1", "jti-A", seeded)

        def explode(*args, **kwargs):
            raise AssertionError("JobStore must not be opened on a cache hit")

        monkeypatch.setattr("jobbuddy.mcp_auth.JobStore", explode)

        token = make_token({"oid": "user-1", "jti": "jti-A"})
        result = resolve_account(token)
        assert result is seeded

    def test_new_jti_re_upserts(self, store: JobStore, monkeypatch):
        """A rotated token (new jti) for the same human must miss the cache
        and re-UPSERT so snapshot fields refresh."""
        monkeypatch.setattr("jobbuddy.mcp_auth.get_settings", lambda: MagicMock(auth_provider="entra"))
        first = resolve_account(make_token({"oid": "user-1", "name": "Old", "jti": "jti-A"}))
        second = resolve_account(make_token({"oid": "user-1", "name": "New", "jti": "jti-B"}))
        assert second.id == first.id
        assert second.display_name == "New"

    def test_raises_when_auth_provider_unset(self, monkeypatch):
        monkeypatch.setattr("jobbuddy.mcp_auth.get_settings", lambda: MagicMock(auth_provider=None))
        with pytest.raises(RuntimeError, match="JOBBUDDY_AUTH_PROVIDER"):
            resolve_account(make_token({"oid": "x", "jti": "j"}))

    def test_raises_when_external_id_missing(self, monkeypatch):
        """Entra refuses the silent oid->sub fallback (handled by
        pick_external_id), so a token with only `sub` raises here rather
        than fragmenting the human across account rows."""
        monkeypatch.setattr("jobbuddy.mcp_auth.get_settings", lambda: MagicMock(auth_provider="entra"))
        with pytest.raises(RuntimeError, match="stable identifier"):
            resolve_account(make_token({"sub": "entra-sub", "jti": "j"}))
