"""Tests for the MCP server run-mode auth builders.

`build_azure_auth()` is not unit-tested — it needs a live Redis. `build_github_auth()`
is pure config (no network in GitHubProvider.__init__), so it's cheap to cover.
`main()` itself is the server runner and is validated live, not here.
"""

import pytest

from jobbuddy.mcp_server import build_github_auth


def test_build_github_auth_reads_env(monkeypatch):
    from fastmcp.server.auth.providers.github import GitHubProvider

    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.testclientid")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "testsecret")
    monkeypatch.setenv("BASE_URL", "https://devbox.example.ts.net:8443")

    auth = build_github_auth()

    assert isinstance(auth, GitHubProvider)


def test_build_github_auth_requires_client_id(monkeypatch):
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "testsecret")

    with pytest.raises(KeyError):
        build_github_auth()
