"""FastMCP authentication glue: turns a verified OAuth token into an
`Account` row, exposes `CurrentAccount()` as a dependency-injection point
for tools.

Tools declare an injected account in their signature:

    @mcp.tool
    def log_job_application(
        ...,
        account: Account = CurrentAccount(),
    ) -> str:
        row = append_row(account_id=account.id, ...)

`CurrentAccount()` raises `RuntimeError` if no token is present — auth is
required for any tool that touches per-account data. The dependency is
stripped from the tool's JSON schema by FastMCP so the calling LLM never
sees it.

Account resolution UPSERTs into Postgres once per token rotation. The
result is cached in-process keyed on `(provider, external_id)` — one
entry per real human, not per token — and the entry tracks the last-seen
JWT id so the cache automatically refreshes whenever a new token arrives.
That keeps the snapshot fields (email/display_name/handle/raw_claims) in
step with the IdP without re-hitting the DB on every tool call.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import cast

from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from uncalled_for import Dependency

from jobbuddy.models import Account
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore

log = logging.getLogger(__name__)


class AccountCache:
    """In-process Account cache keyed on `(provider, external_id)`.

    Bounded by user count (one entry per real human), not by token-rotation
    frequency. Each entry remembers the JWT id (`jti`) that last refreshed
    it; on a new jti we fall through to the DB UPSERT so name/email/handle
    snapshots stay current with the IdP.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[str | None, Account]] = {}
        self._lock = threading.Lock()

    def get(self, provider: str, external_id: str, jti: str | None) -> Account | None:
        with self._lock:
            entry = self._entries.get((provider, external_id))
        if entry is None:
            return None
        cached_jti, account = entry
        # Same token (or both jti-less): cache hit.
        if cached_jti == jti:
            return account
        return None

    def put(self, provider: str, external_id: str, jti: str | None, account: Account) -> None:
        with self._lock:
            self._entries[(provider, external_id)] = (jti, account)

    def invalidate(self, provider: str, external_id: str) -> None:
        with self._lock:
            self._entries.pop((provider, external_id), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


account_cache = AccountCache()


def resolve_account(token: AccessToken) -> Account:
    """Resolve or create the Account row matching this token's claims.

    Cache lookup keys on `(provider, external_id)` paired with the token's
    `jti`: hits return the cached Account; misses fall through to the
    Postgres UPSERT, which refreshes email/display_name/handle/raw_claims.
    Result: one DB write per token rotation per human, regardless of how
    many tool calls happen on a given token.
    """
    auth_provider = get_settings().auth_provider
    if auth_provider is None:
        raise RuntimeError(
            "JOBBUDDY_AUTH_PROVIDER is not set but an authenticated request "
            "arrived. The MCP server must be started with auth_provider "
            "configured ('entra' or 'github')."
        )

    from jobbuddy.store import pick_external_id
    external_id = pick_external_id(auth_provider, token.claims)
    if not external_id:
        raise RuntimeError(
            f"Cannot resolve account: token claims missing the stable identifier "
            f"required for provider={auth_provider!r}."
        )

    jti = token.claims.get("jti")
    cached = account_cache.get(auth_provider, external_id, jti)
    if cached is not None:
        return cached

    with JobStore() as store:
        account = store.upsert_account_from_claims(auth_provider, token.claims)

    account_cache.put(auth_provider, external_id, jti, account)
    log.debug(
        "resolved account id=%s provider=%s external_id=%s jti=%s",
        account.id, account.provider, account.external_id, jti,
    )
    return account


class CurrentAccountDependency(Dependency[Account]):
    """Async context manager for Account dependency — see CurrentAccount()."""

    async def __aenter__(self) -> Account:
        token = get_access_token()
        if token is None:
            raise RuntimeError(
                "No access token found. The MCP server requires authentication "
                "for tools that touch per-account data."
            )
        return resolve_account(token)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentAccount() -> Account:
    """FastMCP dependency that resolves the Account for the current request.

    Raises RuntimeError if no authentication is present. Use as a default
    value on a tool parameter:

        @mcp.tool
        def my_tool(arg: str, account: Account = CurrentAccount()) -> str:
            ...

    The injected parameter is stripped from the tool's JSON schema, so the
    calling LLM never sees `account` and cannot try to populate it.
    """
    return cast(Account, CurrentAccountDependency())
