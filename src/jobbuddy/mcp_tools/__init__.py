"""MCP tool registrations.

Importing this package registers every tool and resource on the shared
`jobbuddy.mcp_tools.app.mcp` FastMCP instance. The split is by user-facing
tool group (jobs, activity, companies, watchlists, resources) — each module
owns one slice of the public MCP surface.

`mcp_server.main()` imports this package once at startup so all tool
decorators run before `mcp.run()` is called.
"""

from jobbuddy.mcp_tools.app import mcp

# Side-effect imports register tools/resources on `mcp`. Order doesn't matter.
from jobbuddy.mcp_tools import (  # noqa: F401, E402  (registration imports)
    activity,
    companies,
    jobs,
    resources,
    watchlists,
)

__all__ = ["mcp"]
