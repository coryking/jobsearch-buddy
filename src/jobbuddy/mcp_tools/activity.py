"""Activity-log tools: log applications, log freeform activity, review history."""

from datetime import date
from typing import Annotated

from pydantic import Field

from jobbuddy.core import (
    fetch_by_id,
    fetch_from_url,
    is_supported_ats_url,
    parse_duration_to_date,
    save_job_listing,
)
from jobbuddy.job_log import append_row, find_by_company, find_duplicates, read_log, unique_companies
from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.mcp_tools.helpers import VALID_ACTIONS, compact_json
from jobbuddy.models import (
    Account,
    ActivityAging,
    ActivityDetail,
    ActivityTimeline,
)
from jobbuddy.registry import ensure_company, lookup_by_name

_VALID_VIEWS = {"aging", "company", "timeline"}


@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
})
def log_job_application(
    url: Annotated[str, Field(default="", description="Job listing URL (Greenhouse, Ashby, Lever, etc.). Only use when user pastes a URL — prefer company + job_id when you already have them.")] = "",
    company: Annotated[str, Field(default="", description="Company name or slug from the registry. Pair with job_id.")] = "",
    job_id: Annotated[str, Field(default="", description="ATS-specific job identifier (pair with company)")] = "",
    action: Annotated[str, Field(default="Application", description="The activity type: Application, Contact, Screen, Interview, Referral, Reach-out, or Inquery")] = "Application",
    person: Annotated[str, Field(default="", description="Contact person's name, if applicable")] = "",
    notes: Annotated[str, Field(default="", description="Free-text notes about this activity")] = "",
    log_date: Annotated[str, Field(default="", description="Date in YYYY-MM-DD format (defaults to today)")] = "",
    account: Account = CurrentAccount(),
) -> str:
    """Record a job application. Fetches job details, saves the listing,
    and appends to the job search tracking log.

    Two ways to identify the job:
    1. url — parses out company + job_id automatically (Greenhouse, Ashby, Lever, etc.)
    2. company + job_id — pass directly (e.g. from search_jobs results)

    Prefer company + job_id when you already have them (e.g. from search_jobs).
    Only fall back to URL when the user pastes a link directly.

    Use when the user says "I applied", "log this application", "log this", or
    "record this". For freeform activity without a job we can look up (recruiter calls,
    networking), use log_job_activity instead. Warns on duplicates but still logs
    (follow-up actions like screens or interviews on the same job are expected)."""
    if action not in VALID_ACTIONS:
        return f"Error: Invalid action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    if url and not (company and job_id):
        try:
            result = fetch_from_url(url)
        except ValueError:
            return (
                "Error: Could not parse company or job_id from this URL. "
                "Retry with company + job_id instead — you can get these from search_jobs results. "
                "For freeform logging without a job_id, use log_job_activity."
            )
    elif company and job_id:
        try:
            result = fetch_by_id(company, job_id)
        except ValueError as e:
            return f"Error: {e}"
    else:
        return "Error: Provide either a URL or company + job_id. For freeform logging, use log_job_activity."

    warnings = []
    dupes = find_duplicates(account.id, url=url)
    if dupes:
        dupe_info = "; ".join(
            f"{d.get('date', '?')} {d.get('action', '?')}" for d in dupes
        )
        warnings.append(f"Duplicate found (logging anyway): {dupe_info}")

    save_job_listing(result.company, result.job)

    row = append_row(
        account.id,
        company=result.company.name,
        role=result.job.title,
        action=action,
        job_id=result.job.id,
        location=result.job.location or "",
        url=url,
        person=person,
        notes=notes,
        row_date=log_date or None,
    )

    output = {
        "status": "ok",
        "action": row["action"],
        "company": result.company.name,
        "role": result.job.title,
        "job_id": result.job.id,
        "location": result.job.location or "",
        "date": row["date"],
    }
    if warnings:
        output["warnings"] = warnings
    return compact_json(output)


@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
})
def log_job_activity(
    company: Annotated[str, Field(description="Canonical company name (use ats://companies registry name if the company exists there)")],
    role: Annotated[str, Field(description="The job title or role name")],
    action: Annotated[str, Field(description="The activity type: Application, Contact, Screen, Interview, Referral, Reach-out, or Inquery")],
    person: Annotated[str, Field(default="", description="Contact person's name, if applicable")] = "",
    url: Annotated[str, Field(default="", description="Job listing or LinkedIn profile URL")] = "",
    notes: Annotated[str, Field(default="", description="Free-text notes about this activity")] = "",
    location: Annotated[str, Field(default="", description="Job location (e.g. 'Seattle, WA' or 'Remote')")] = "",
    job_id: Annotated[str, Field(default="", description="ATS job ID, if known")] = "",
    log_date: Annotated[str, Field(default="", description="Date in YYYY-MM-DD format (defaults to today)")] = "",
    account: Account = CurrentAccount(),
) -> str:
    """Log any job search activity — contacts, referrals, screens, interviews, reach-outs.

    Use when the user mentions a recruiter call, interview, referral, networking contact,
    or any job search activity where you do NOT have a job_id. Examples: "I talked to a
    recruiter at Stripe", "log my interview with Plaid", "I reached out to someone at Google."
    If you have a job_id (from search_jobs or a URL), use log_job_application instead.

    Required for WA state unemployment audit compliance: date, company, role, action,
    and at least one contact method (url or person name)."""
    if action not in VALID_ACTIONS:
        return f"Error: Invalid action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    if not company or not role:
        return "Error: company and role are required."

    if not url and not person:
        return "Error: At least one contact method required (url or person) for unemployment audit compliance."

    if url and is_supported_ats_url(url):
        return (
            "Error: Use log_job_application instead — pass the URL directly, or "
            "pass company + job_id from search_jobs results."
        )

    row = append_row(
        account.id,
        company=company,
        role=role,
        action=action,
        job_id=job_id,
        person=person,
        location=location,
        url=url,
        notes=notes,
        row_date=log_date or None,
    )

    return compact_json({
        "status": "ok",
        "action": row["action"],
        "company": company,
        "role": role,
        "location": location,
        "date": row["date"],
    })


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def job_activity(
    view: Annotated[str, Field(
        default="aging",
        description=(
            "Which view to return. "
            "'aging' (default): companies grouped by time since last activity — "
            "0-30 days, 31-60, 61-90, 90+. "
            "'company': full chronological detail for one company (requires the company param). "
            "'timeline': flat reverse-chronological log of all activities."
        ),
    )] = "aging",
    since: Annotated[str, Field(
        default="",
        description=(
            "Only include activity from this date forward. "
            "Accepts durations (24h, 3d, 2w, 6m, 1y) or ISO dates (YYYY-MM-DD). "
            "Omit for all time."
        ),
    )] = "",
    action: Annotated[str, Field(
        default="",
        description=(
            "Filter to one activity type: Application, Contact, Screen, "
            "Interview, Referral, Reach-out, or Inquery. Omit for all types."
        ),
    )] = "",
    company: Annotated[str, Field(
        default="",
        description="Company name or slug. Required for view='company'; narrows other views to one company.",
    )] = "",
    account: Account = CurrentAccount(),
) -> str:
    """Review job search activity — applications, screens, interviews, contacts.

    Use when the user asks about their job search history: "what have I applied to",
    "who should I follow up with", "show my activity", "what's my history with X",
    "any companies I haven't touched in a while".

    Views:
    - aging (default): companies grouped by recency of last activity
    - company: full detail for one company
    - timeline: flat reverse-chronological event log

    All views accept since and action filters to narrow the result."""
    view = view.strip().lower()
    if view not in _VALID_VIEWS:
        return f"Error: Invalid view '{view}'. Must be one of: {', '.join(sorted(_VALID_VIEWS))}"

    action = action.strip()
    if action and action not in VALID_ACTIONS:
        return f"Error: Invalid action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    # Parse since → date
    since_date: date | None = None
    if since.strip():
        try:
            since_date = date.fromisoformat(parse_duration_to_date(since))
        except ValueError as e:
            return f"Error: {e}"

    # Route by view
    if view == "company":
        if not company:
            return "Error: view='company' requires the company parameter."
        return _company_view(account, company, since_date, action or None)
    elif view == "timeline":
        return _timeline_view(account, since_date, action or None, company.strip() or None)
    else:
        return _aging_view(account, since_date, action or None, company.strip() or None)


def _aging_view(
    account: Account,
    since_date: date | None,
    action_filter: str | None,
    company_filter: str | None,
) -> str:
    rows = read_log(account.id, since=since_date, action=action_filter)

    if company_filter:
        resolved = lookup_by_name(company_filter)
        display_name = resolved.name if resolved else company_filter
        rows = [r for r in rows if r.get("company", "").lower() == display_name.lower()]
        if not rows:
            rows = [r for r in read_log(account.id, since=since_date, action=action_filter)
                    if r.get("company", "").lower() == company_filter.lower()]

    for name in unique_companies(account.id):
        ensure_company(name)

    by_company: dict[str, list[dict]] = {}
    for row in rows:
        co = row.get("company", "").strip()
        if co:
            by_company.setdefault(co, []).append(row)

    return ActivityAging.from_log(by_company).to_mcp_result()


def _timeline_view(
    account: Account,
    since_date: date | None,
    action_filter: str | None,
    company_filter: str | None,
) -> str:
    rows = read_log(account.id, since=since_date, action=action_filter)

    if company_filter:
        resolved = lookup_by_name(company_filter)
        display_name = resolved.name if resolved else company_filter
        rows = [r for r in rows if r.get("company", "").lower() == display_name.lower()]
        if not rows:
            rows = [r for r in read_log(account.id, since=since_date, action=action_filter)
                    if r.get("company", "").lower() == company_filter.lower()]

    return ActivityTimeline.from_rows(rows).to_mcp_result()


def _company_view(
    account: Account,
    company: str,
    since_date: date | None,
    action_filter: str | None,
) -> str:
    rows = read_log(account.id, since=since_date, action=action_filter)

    resolved = lookup_by_name(company)
    display_name = resolved.name if resolved else company

    company_rows = [r for r in rows if r.get("company", "").lower() == display_name.lower()]
    if not company_rows:
        company_rows = [r for r in rows if r.get("company", "").lower() == company.lower()]

    if not company_rows:
        return f"No activity found for '{company}'. Try job_activity() with no company to see all."

    ensure_company(display_name)
    return ActivityDetail.from_company(display_name, company_rows).to_mcp_result()
