"""The withdraw-don't-delete contract for the corpus-backed MCP surface.

`mcp_tools/__init__.py` promises that re-adding one import line restores
the withdrawn tools (jobs.py, companies.py, watchlists.py). Nothing else
imports those modules anymore, so without this test they'd rot silently
and the revert would break exactly when someone reaches for it.

The import runs in a subprocess because importing a withdrawn module
registers its tools on the shared `mcp` singleton — polluting every other
test that asserts the registered surface.
"""

import subprocess
import sys

WITHDRAWN_MODULES = ("jobs", "companies", "watchlists")


def test_withdrawn_tool_modules_still_import():
    code = "; ".join(
        f"import jobbuddy.mcp_tools.{m}" for m in WITHDRAWN_MODULES
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"Withdrawn MCP modules no longer import — the one-line revert "
        f"promised in mcp_tools/__init__.py is broken:\n{result.stderr}"
    )
