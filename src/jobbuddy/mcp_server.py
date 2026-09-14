"""MCP server entry point: wires auth, telemetry, and starts FastMCP.

`main()` selects a run mode: devbox HTTP (GitHub OAuth, gated on
`GITHUB_OAUTH_CLIENT_ID`), or bare stdio (no auth).

Tool and resource implementations live in `jobbuddy.mcp_tools.*`. Importing
that package registers everything on the shared `mcp` instance defined in
`jobbuddy.mcp_tools.app`. This module owns server-lifecycle concerns only —
auth provider, the `account`-leak safety check, and the `main()` entry point
referenced by the `jsb-mcp` console script.
"""

import logging
import os

from jobbuddy.mcp_tools import mcp  # importing registers all tools/resources

log = logging.getLogger(__name__)


def build_github_auth():
    """Build GitHubProvider for the devbox HTTP deployment.

    One long-lived devbox process backs OAuth/DCR state in memory — no Redis.
    Reads GITHUB_OAUTH_* and BASE_URL from the environment.
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
    discovering it the hard way.

    Sweeps every registered tool rather than a hardcoded list, so
    restoring the withdrawn corpus modules (or adding new authenticated
    tools) can never outrun the guard."""
    leaked: list[str] = []
    for tool in await mcp.list_tools():
        schema = tool.parameters
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if "account" in props:
            leaked.append(tool.name)
    if leaked:
        raise RuntimeError(
            f"`account` parameter leaked into tool input schema for: {leaked}. "
            f"FastMCP DI parameter-stripping is broken — refusing to start. "
            f"Verify `fastmcp` is at the version pinned in pyproject.toml."
        )


def main():
    import asyncio

    from jobbuddy.settings import get_settings

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if os.environ.get("GITHUB_OAUTH_CLIENT_ID"):
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
