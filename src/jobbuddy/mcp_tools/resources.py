"""MCP resources — read-only views over the activity log, registry, and
supported-domain list."""

import json

from jobbuddy.core import SUPPORTED_DOMAINS
from jobbuddy.job_log import read_log
from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.models import Account
from jobbuddy.registry import list_companies, lookup_by_name


@mcp.resource("ats://log")
def get_log(account: Account = CurrentAccount()) -> str:
    """Raw job search activity log for the current account, as JSON. Prefer
    review_activity_log tool instead — it provides per-company summaries,
    filtering, and pivot stats. This resource returns unprocessed rows."""
    return json.dumps(read_log(account.id), indent=2)


@mcp.resource("ats://companies")
def get_companies() -> str:
    """The company phone book: every registered company as slug, name, and
    ATS type — compact on purpose, cheap to read whole. Use it to resolve
    a name for `list_company_jobs`. For a bio ('tell me about X'), read
    ats://companies/{slug} instead of this list."""
    rows = [
        {"slug": c.slug, "name": c.name, "ats": c.ats}
        for c in list_companies().values()
    ]
    return json.dumps(rows, indent=2)


@mcp.resource("ats://companies/{slug}")
def get_company(slug: str) -> str:
    """One company's full registry record — slug, name, ATS config, and a
    60-100 word NPOV short_bio. Use for 'tell me about X' / 'what does X
    do' triage. Bio may be null for unresearched companies — fall back to
    web search for those."""
    company = lookup_by_name(slug)
    if not company:
        return json.dumps({"error": f"Unknown company: {slug}"})
    return json.dumps(
        company.model_dump(include={"slug", "name", "ats", "board", "short_bio"}),
        indent=2,
    )


@mcp.resource("ats://supported-domains")
def get_supported_domains() -> str:
    """URL domain patterns recognized by lookup_job and log_application. Use to decide
    whether a job URL should go to log_application (supported ATS) or log_entry (other)."""
    return json.dumps(SUPPORTED_DOMAINS, indent=2)
