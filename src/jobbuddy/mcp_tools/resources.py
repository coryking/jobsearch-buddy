"""MCP resources — read-only views over the activity log, registry, and
supported-domain list."""

import json

from jobbuddy.core import SUPPORTED_DOMAINS
from jobbuddy.job_log import read_log
from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.models import Account
from jobbuddy.registry import list_companies


@mcp.resource("ats://log")
def get_log(account: Account = CurrentAccount()) -> str:
    """Raw job search activity log for the current account, as JSON. Prefer
    review_activity_log tool instead — it provides per-company summaries,
    filtering, and pivot stats. This resource returns unprocessed rows."""
    return json.dumps(read_log(account.id), indent=2)


@mcp.resource("ats://companies")
def get_companies() -> str:
    """Use when the user asks 'tell me about X', 'what does X do', or wants
    to triage which companies are worth a closer look before searching jobs.

    Returns slug, name, ATS config, and a 60-100 word NPOV short_bio per
    company. Bio may be null for unresearched companies — fall back to web
    search for those."""
    rows = {
        slug: c.model_dump(include={"slug", "name", "ats", "board", "short_bio"})
        for slug, c in list_companies().items()
    }
    return json.dumps(rows, indent=2)


@mcp.resource("ats://supported-domains")
def get_supported_domains() -> str:
    """URL domain patterns recognized by lookup_job and log_application. Use to decide
    whether a job URL should go to log_application (supported ATS) or log_entry (other)."""
    return json.dumps(SUPPORTED_DOMAINS, indent=2)
