# Azure Migration: MCP Server on Azure Functions

Remote MCP server deployment using Azure Functions (Flex Consumption)
with FastMCP AzureProvider (Entra ID OAuth via OAuthProxy pattern).

## Architecture

```
Claude (mobile/web/desktop)
    │ HTTPS (port 443)
    │
    │ 1. POST /mcp → 401
    │ 2. GET /.well-known/oauth-protected-resource/mcp → PRM document
    │ 3. GET /.well-known/oauth-authorization-server → auth metadata + DCR endpoint
    │ 4. POST /register → Dynamic Client Registration (OAuthProxy handles)
    │ 5. GET /authorize → consent screen → redirect to Entra login
    │ 6. Entra login → callback to /auth/callback
    │ 7. OAuthProxy exchanges code for tokens, issues FastMCP JWT
    │ 8. POST /mcp with Bearer token → tool calls
    ▼
Azure Functions (Flex Consumption, Python 3.12)
    │ FastMCP AzureProvider (OAuthProxy pattern)
    │ stateless_http=True — no server-side sessions
    │ User identity from Entra JWT claims
    ▼
FastMCP server
    │
    ├──→ Azure Managed Redis (Balanced B0)
    │     OAuth state: DCR registrations, auth codes, JTI mappings, tokens
    │     key_value RedisStore, managed identity auth
    │
    ├──→ Azure PostgreSQL Flexible Server (B1ms)
    │     Job data: listings, embeddings, search
    │     asyncpg, managed identity auth (Entra token as password)
    │
    ▼
jobsearchbuddy database
```

## Key Decisions

### Auth: FastMCP AzureProvider, NOT Easy Auth

Easy Auth (App Service Authentication) intercepts requests at the platform
level and serves its own PRM/401 responses. Claude's connector does Dynamic
Client Registration (DCR) to authenticate, but Entra doesn't support DCR.

FastMCP's `AzureProvider` (subclass of `OAuthProxy`) solves this by:
- Presenting a DCR-compliant interface to Claude
- Using pre-registered Entra app credentials for the upstream OAuth flow
- Handling scope prefixing, token version, and Entra quirks automatically
- Issuing its own JWTs to clients (not forwarding Entra tokens)

The Entra app registration and client secret are created by Terraform
(`azuread_application` + `azuread_application_password`). No manual steps.

### Stateless Mode + Redis-Backed OAuth State

`stateless_http=True` passed to `mcp.run()`. No server-side sessions.
Required for Functions' scale-to-zero and horizontal scaling.

AzureProvider needs persistent state for DCR client registrations, auth
codes, JTI mappings, and token storage. Without external storage, state
is lost across worker processes (Flex Consumption runs multiple workers
per instance and doesn't allow setting `FUNCTIONS_WORKER_PROCESS_COUNT`).

Solution: Redis (via `key_value` library's RedisStore) as `client_storage`
on AzureProvider. Single connection, no pool, built-in TTL. All workers
share the same Redis instance. Azure Managed Redis Balanced B0 (~$12/mo).

PostgreSQLStore was tried first but failed: `asyncpg.create_pool()` defaults
to `min_size=10` per pool × ~5 Flex Consumption workers = 50 connections,
exhausting B1ms Postgres's slot limit. PgBouncer only on General Purpose
(~$100/mo). Creating the pool at module load time also conflicts with
uvicorn's event loop.

```python
from key_value.aio.stores.redis import RedisStore

redis_store = RedisStore(url=redis_url)
auth = AzureProvider(..., client_storage=redis_store)
mcp = FastMCP("server", auth=auth)
mcp.run(transport="streamable-http", stateless_http=True)
```

### PostgreSQL Managed Identity Auth

PostgreSQL is used for job data (not OAuth state). Connects using Entra
managed identity — no passwords. An Entra access token is used as the
password:

```python
from azure.identity import DefaultAzureCredential
credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")
url = f"postgresql://{identity_name}:{token.token}@{host}:5432/{db}?sslmode=require"
```

Important: username is the managed identity **name** (e.g. `id-mcp-xxx`),
not the client ID. Use `sslmode=require` (asyncpg), not `ssl=require`.

Token expiry (~1 hour): for production, needs a token-refreshing connection
factory (bluetaka uses SQLAlchemy's `creator` pattern). The managed identity
Postgres role is created via `pgaadauth_create_principal` — must run
against the `postgres` database, not the user database.

### Python 3.12

Flex Consumption runs 3.12 (set in Terraform). The CLI claims 3.14
support but Flex Consumption's `functionAppConfig.runtime` only reliably
supports up to 3.12. `uv.lock` is generated against 3.12.

### Custom Handler Profile

`host.json` uses `configurationProfile: "mcp-custom-handler"` which
auto-configures catch-all routing, empty route prefix, and HTTP proxying.
Required app setting: `AzureWebJobsFeatureFlags=EnableMcpCustomHandlerPreview`

### Dependency Management

Deploy via `azd deploy` which handles `pyproject.toml` + `uv.lock` via
Oryx build. `func publish` doesn't work for custom handlers (doesn't
install Python deps). Terraform sets
`PYTHONPATH=/home/site/wwwroot/.python_packages/lib/site-packages` as
an app setting — custom handlers don't get automatic PYTHONPATH.

## Infrastructure

Production infrastructure is in `infrastructure/` (Terraform). See the
`.tf` files for resource definitions, app settings, and provider config.

PoC (Bicep) lives in `~/projects/mcp-hello-world/` — still active, not
yet cleaned up.

## Gotchas

- **Flex Consumption runtime version** is in `functionAppConfig.runtime`,
  NOT `siteConfig.linuxFxVersion`. Can't change after creation via CLI.
- **`func publish --python`** doesn't install deps for custom handlers.
  Use `azd deploy` instead.
- **FastMCP 3.x vs `mcp` SDK divergence**: `stateless_http` moved from
  constructor to `run()`. The Microsoft sample uses `mcp` SDK, we use
  `fastmcp`.
- **PRM path**: `/.well-known/oauth-protected-resource/mcp` (with suffix),
  not `/.well-known/oauth-protected-resource`.
- **OAuth state needs external storage**: default FileTreeStore doesn't
  survive across Flex Consumption workers. Use Redis.
- **Multiple workers per instance**: Flex Consumption doesn't allow setting
  `FUNCTIONS_WORKER_PROCESS_COUNT`. Can't force single worker.
- **Don't overload well-known env vars**: setting `AZURE_CLIENT_SECRET`
  alongside `AZURE_CLIENT_ID` makes DefaultAzureCredential use
  ClientSecretCredential instead of managed identity. Use `ENTRA_OAUTH_*`
  prefixed vars for the OAuth app credentials.

## Troubleshooting

### Common Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| 502 Bad Gateway | Custom handler not starting | Check App Insights for startup errors |
| `ModuleNotFoundError` | PYTHONPATH not set or wrong Python version | Verify `PYTHONPATH` app setting, check runtime version |
| `no pg_hba.conf entry ... no encryption` | Missing `sslmode=require` | Use `sslmode=require` (asyncpg), not `ssl=require` |
| `password authentication failed` | Wrong Postgres username (client ID vs name) | Username must be identity NAME, not client ID |
| `MSI identity should not use ClientSecretCredential` | `AZURE_CLIENT_SECRET` in env alongside `AZURE_CLIENT_ID` | Use `ENTRA_OAUTH_*` prefixed vars instead |
| `remaining connection slots reserved` | asyncpg default pool `min_size=10` × N workers | Use Redis for OAuth state, not Postgres |
| `Authorization code not found` on `/token` | Auth state lost between workers | Use RedisStore as `client_storage` |

## Remaining Work

- Port MCP server code from PoC (`~/projects/mcp-hello-world/`) to this repo
- Connect to production Postgres (Azure) instead of devbox
- Data migration from devbox to Azure Postgres
- Clean up PoC resources (`~/projects/mcp-hello-world/`, `rg-jsb-mcp-poc-v3`)
- Token-refreshing Postgres connection factory
- Deployment pipeline (GitHub Actions or `azd deploy`)
