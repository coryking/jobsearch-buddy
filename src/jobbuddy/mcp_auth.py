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

Account resolution UPSERTs into Postgres on first call per token; results
are cached in-process keyed on the FastMCP JWT id (`jti`) so subsequent
tool calls in the same session don't re-hit the DB.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import cast

from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from uncalled_for import Dependency

from jobbuddy.models import Account
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore

log = logging.getLogger(__name__)

# Cache key: claims["jti"] (FastMCP-issued JWT id, unique per token).
# Falls back to the raw token string when jti is absent (shouldn't happen
# for OAuthProxy-issued tokens, but bearer-only test setups may skip it).
_account_cache: dict[str, Account] = {}


def resolve_account(token: AccessToken) -> Account:
    """Resolve or create the Account row matching this token's claims.

    UPSERTs by (provider, external_id), refreshing email/display_name/
    handle/raw_claims from the latest token. Cached in-process per token
    so the round trip happens once per session, not once per tool call.
    """
    cache_key = token.claims.get("jti") or token.token
    cached = _account_cache.get(cache_key)
    if cached is not None:
        return cached

    auth_provider = get_settings().auth_provider
    if auth_provider is None:
        raise RuntimeError(
            "JOBBUDDY_AUTH_PROVIDER is not set but an authenticated request "
            "arrived. The MCP server must be started with auth_provider "
            "configured ('entra' or 'github')."
        )

    with JobStore() as store:
        account = store.upsert_account_from_claims(auth_provider, token.claims)

    _account_cache[cache_key] = account
    log.debug(
        "resolved account id=%s provider=%s display=%s",
        account.id, account.provider, account.display_name,
    )
    return account


class _CurrentAccount(Dependency[Account]):
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
    return cast(Account, _CurrentAccount())
