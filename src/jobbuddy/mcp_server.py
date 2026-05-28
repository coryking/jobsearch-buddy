"""MCP server entry point: wires auth, telemetry, and starts FastMCP.

`main()` selects a run mode: Azure Functions (Entra OAuth + Redis, gated on
`ENTRA_OAUTH_CLIENT_ID`), devbox HTTP (GitHub OAuth, no Redis, gated on
`GITHUB_OAUTH_CLIENT_ID`), or bare stdio (no auth).

Tool and resource implementations live in `jobbuddy.mcp_tools.*`. Importing
that package registers everything on the shared `mcp` instance defined in
`jobbuddy.mcp_tools.app`. This module owns server-lifecycle concerns only —
auth provider, OpenTelemetry wiring, the `account`-leak safety check, and
the `main()` entry point referenced by the `jsb-mcp` console script.
"""

import logging
import os

from jobbuddy.mcp_tools import mcp  # importing registers all tools/resources

log = logging.getLogger(__name__)


def build_azure_auth():
    """Build AzureProvider with Redis-backed state for Azure Functions deployment.

    Reads ENTRA_OAUTH_* env vars for OAuth config and uses managed identity
    (AZURE_CLIENT_ID) to authenticate to Azure Managed Redis for state storage.
    """
    from fastmcp.server.auth.providers.azure import AzureProvider
    from key_value.aio.stores.redis import RedisStore
    from redis.asyncio import Redis

    from jobbuddy.settings import get_azure_token

    oauth_client_id = os.environ["ENTRA_OAUTH_CLIENT_ID"]
    oauth_client_secret = os.environ["ENTRA_OAUTH_CLIENT_SECRET"]
    oauth_tenant_id = os.environ["ENTRA_OAUTH_TENANT_ID"]
    oauth_identifier_uri = os.environ.get("ENTRA_OAUTH_IDENTIFIER_URI")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

    redis_host = os.environ.get("REDIS_HOST", "")
    redis_port = int(os.environ.get("REDIS_PORT", "10000"))
    managed_identity_client_id = os.environ.get("AZURE_CLIENT_ID", "")

    if not redis_host:
        raise RuntimeError("REDIS_HOST not set — cannot initialize OAuth state store")
    if not managed_identity_client_id:
        raise RuntimeError("AZURE_CLIENT_ID not set — cannot authenticate to Redis")

    log.info("Acquiring Entra token for Redis")
    token = get_azure_token("https://redis.azure.com/.default")

    client = Redis(
        host=redis_host,
        port=redis_port,
        ssl=True,
        username=managed_identity_client_id,
        password=token,
        decode_responses=True,
    )

    redis_store = RedisStore(
        client=client,
        default_collection="mcp_oauth_state",
    )

    auth = AzureProvider(
        client_id=oauth_client_id,
        client_secret=oauth_client_secret,
        tenant_id=oauth_tenant_id,
        required_scopes=["user_impersonation"],
        base_url=base_url,
        identifier_uri=oauth_identifier_uri or None,
        client_storage=redis_store,
    )

    log.info("AzureProvider initialized with RedisStore (host=%s)", redis_host)
    return auth


def build_github_auth():
    """Build GitHubProvider for the devbox HTTP deployment.

    Unlike the Azure path, one long-lived devbox process backs OAuth/DCR
    state in memory — no Redis, no Entra. Reads GITHUB_OAUTH_* and BASE_URL
    from the environment.
    """
    from fastmcp.server.auth.providers.github import GitHubProvider

    client_id = os.environ["GITHUB_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GITHUB_OAUTH_CLIENT_SECRET"]
    base_url = os.environ.get("BASE_URL", "http://localhost:8001")

    auth = GitHubProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
    )

    log.info("GitHubProvider initialized (base_url=%s)", base_url)
    return auth


async def assert_account_dependency_stripped() -> None:
    """Confirm FastMCP's DI machinery hides the `account` parameter from
    every tool's JSON schema. If a fastmcp regression or downgrade ever
    let `account` slip into the schema, the calling LLM could populate
    it and bypass authentication. Fail loudly at startup rather than
    discovering it the hard way."""
    leaked: list[str] = []
    for tool_name in (
        "log_job_application", "log_job_activity", "search_jobs",
        "find_companies", "review_activity_log",
        "watchlist_create", "watchlist_update", "watchlist_delete",
        "watchlist_list",
    ):
        tool = await mcp.get_tool(tool_name)
        schema = tool.parameters
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if "account" in props:
            leaked.append(tool_name)
    if leaked:
        raise RuntimeError(
            f"`account` parameter leaked into tool input schema for: {leaked}. "
            f"FastMCP DI parameter-stripping is broken — refusing to start. "
            f"Verify `fastmcp` is at the version pinned in pyproject.toml."
        )


def main():
    import asyncio

    from jobbuddy.settings import get_settings

    if os.environ.get("ENTRA_OAUTH_CLIENT_ID"):
        # Configure Azure Monitor telemetry before other setup so logging is captured
        conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if conn_str:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=conn_str,
                disable_offline_storage=True,
            )

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

        # Authenticated tools require an auth_provider so claim shapes can
        # be resolved deterministically. Fail at startup rather than on the
        # first authenticated request.
        if get_settings().auth_provider is None:
            raise RuntimeError(
                "JOBBUDDY_AUTH_PROVIDER must be set (\"entra\" or \"github\") "
                "when the MCP server runs with OAuth configured. Authenticated "
                "tools cannot resolve a claim shape without it."
            )

        asyncio.run(assert_account_dependency_stripped())

        auth = build_azure_auth()
        mcp.auth = auth
        mcp.run(transport="streamable-http", stateless_http=True)
    elif os.environ.get("GITHUB_OAUTH_CLIENT_ID"):
        # Devbox HTTP deployment: GitHub OAuth, local Postgres, no Redis/Entra.
        # Host/port come from FASTMCP_HOST/FASTMCP_PORT (set in the systemd unit
        # so jsb binds localhost:8001 and stays off SMH's :8000).
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

        if get_settings().auth_provider != "github":
            raise RuntimeError(
                "GITHUB_OAUTH_CLIENT_ID is set but JOBBUDDY_AUTH_PROVIDER is not "
                "\"github\". Authenticated tools cannot resolve a claim shape "
                "without it."
            )

        asyncio.run(assert_account_dependency_stripped())

        auth = build_github_auth()
        mcp.auth = auth
        mcp.run(transport="streamable-http", stateless_http=True)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
