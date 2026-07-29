"""MCP tool registrations.

Importing this package registers every tool and resource on the shared
`jobbuddy.mcp_tools.app.mcp` FastMCP instance. The registered surface is
stateless-live (live.py: get_job, list_company_jobs, get_application_form)
plus application tracking (activity.py) and read-only resources.

The corpus-backed tool modules (jobs.py, companies.py, watchlists.py)
remain on disk but are deliberately NOT imported — the stored-search
surface is withdrawn while the operator evaluates whether live fetch
covers the actual usage. Re-adding an import here restores those tools.

`mcp_server.main()` imports this package once at startup so all tool
decorators run before `mcp.run()` is called.
"""

from jobbuddy.mcp_tools.app import mcp

# Side-effect imports register tools/resources on `mcp`. Order doesn't matter.
from jobbuddy.mcp_tools import (  # noqa: F401, E402  (registration imports)
    activity,
    live,
    resources,
)

__all__ = ["mcp"]
