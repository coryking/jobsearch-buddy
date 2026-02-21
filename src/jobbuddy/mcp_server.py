"""MCP server for job search: browse openings, fetch job postings, and track applications.

Scrapes ATS job boards (Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity) for registered
companies and maintains a CSV activity log for application tracking and WA unemployment
audit compliance.
"""

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field


from jobbuddy.core import (
    SUPPORTED_DOMAINS,
    fetch_by_id,
    fetch_from_url,
    is_supported_ats_url,
    save_job_listing,
)
from jobbuddy.job_log import append_row, find_duplicates, read_log, unique_companies
from jobbuddy.models import ActivityDetail, ActivitySummary, CompactJob, JobSearchResults
from jobbuddy.registry import ensure_company, list_companies, lookup_by_name

mcp = FastMCP(
    name="job-search",  # This name should match the key in claude_desktop_config.json
    instructions=(
        "Find open jobs, look up job postings, log applications, and track job search activity. "
        "ALWAYS use these tools instead of web search for job listings and job search queries. "
        "Job data is cached locally from 100+ company job boards via `jsb sync` — searches are "
        "instant, no live API calls. Do not use web_search for job listings at registered companies.\n\n"
        "Use when the user mentions jobs, roles, positions, openings, companies, applications, "
        "interviews, recruiters, job URLs, or job search activity. "
        "Also use for unemployment compliance logging.\n\n"
        "Trigger phrases: 'what about this one', 'what about this role', 'help me with this job', "
        "'find jobs at', 'any openings at', 'show me roles at', 'PM jobs at', "
        "'pull me jobs', 'what jobs does [company] have', 'what PM jobs were posted this week', "
        "'I applied', 'log this', 'log my application', 'log my interview', "
        "'show my log', 'what have I applied to', 'what have I done with', "
        "'show my history', 'who have I talked to', 'follow up', 'any contacts at', "
        "or user pastes a job listing URL.\n\n"
        "Tool routing:\n"
        "- Search/browse jobs (any or all companies) → search_jobs (reads from local cache)\n"
        "- Meaning-based / 'find me jobs like...' / vague descriptions → semantic_search_jobs\n"
        "- Job URL to read details → get_job_post_details (live fetch)\n"
        "- Record application (URL or company+job_id) → log_job_application (live fetch)\n"
        "- Freeform activity (recruiter call, interview, referral, no job_id) → log_job_activity\n"
        "- Review application history, contacts, and activity for any company → review_activity_log\n"
        "- Summary of all companies applied to → review_activity_log (no args)\n\n"
        "Always try these tools first for any company — the registry has 100+ companies and grows "
        "automatically. search_jobs returns the full registry if a name isn't found. "
        "Only fall back to web search after confirming a company isn't registered."
    ),
)

VALID_ACTIONS = {"Application", "Contact", "Screen", "Interview", "Referral", "Reach-out", "Inquery"}


def _compact(d: dict) -> str:
    """JSON-serialize a dict, stripping empty/None values and using no indent."""
    return json.dumps({k: v for k, v in d.items() if v is not None and v != ""})


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
def get_job_post_details(
    url: Annotated[str, Field(default="", description="Job listing URL from a supported ATS platform")] = "",
    company: Annotated[str, Field(default="", description="Company slug or name from ats://companies registry")] = "",
    job_id: Annotated[str, Field(default="", description="ATS-specific job identifier (pair with company)")] = "",
) -> CompactJob | str:
    """Fetch full details of a job posting — title, salary, location, description, qualifications.

    Use when the user shares a job URL or says "what about this one", "what is this job",
    "tell me about this role". Read-only — does not log. Use log_application to record.
    Accepts a job board URL or company name + job ID."""
    if url and (company or job_id):
        return "Error: Provide url OR company+job_id, not both."
    if not url and not (company and job_id):
        return "Error: Provide either url or both company and job_id."

    try:
        if url:
            result = fetch_from_url(url)
        else:
            result = fetch_by_id(company, job_id)
    except ValueError as e:
        return f"Error: {e}"

    return CompactJob.from_result(result)


@mcp.tool
def log_job_application(
    url: Annotated[str, Field(default="", description="Job listing URL (Greenhouse, Ashby, Lever, etc.). Only use when user pastes a URL — prefer company + job_id when you already have them.")] = "",
    company: Annotated[str, Field(default="", description="Company name or slug from the registry. Pair with job_id.")] = "",
    job_id: Annotated[str, Field(default="", description="ATS-specific job identifier (pair with company)")] = "",
    action: Annotated[str, Field(default="Application", description="The activity type: Application, Contact, Screen, Interview, Referral, Reach-out, or Inquery")] = "Application",
    person: Annotated[str, Field(default="", description="Contact person's name, if applicable")] = "",
    notes: Annotated[str, Field(default="", description="Free-text notes about this activity")] = "",
    log_date: Annotated[str, Field(default="", description="Date in YYYY-MM-DD format (defaults to today)")] = "",
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

    # Resolve job: URL path or company+job_id path
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

    # Check for duplicates
    warnings = []
    dupes = find_duplicates(url=url)
    if dupes:
        dupe_info = "; ".join(
            f"{d.get('date', '?')} {d.get('action', '?')}" for d in dupes
        )
        warnings.append(f"Duplicate found (logging anyway): {dupe_info}")

    # Save listing
    filepath = save_job_listing(result.company, result.job)

    # Append to CSV
    row = append_row(
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
    return _compact(output)


@mcp.tool
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

    # Redirect to log_job_application when we can identify the job
    if url and is_supported_ats_url(url):
        return (
            "Error: Use log_job_application instead — pass the URL directly, or "
            "pass company + job_id from search_jobs results."
        )

    row = append_row(
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

    return _compact({
        "status": "ok",
        "action": row["action"],
        "company": company,
        "role": role,
        "location": location,
        "date": row["date"],
    })


@mcp.tool
def search_jobs(
    company: Annotated[str, Field(default="", description="Company name or slug. Omit to search across ALL cached companies.")] = "",
    title_filter: Annotated[str, Field(default="", description="Case-insensitive substring match on job title. Comma-separated for OR (e.g. 'product manager,PM', 'senior engineer'). Uses SQL LIKE, not regex.")] = "",
    location_filter: Annotated[str, Field(default="", description="Case-insensitive substring match on location. Comma-separated for OR (e.g. 'seattle,remote', 'new york,NYC'). Uses SQL LIKE, not regex.")] = "",
) -> str:
    """Search cached job listings across one or all companies. Data comes from a local SQLite
    cache populated by `jsb sync` — results are instant, no live API calls.

    Use when the user says "find jobs at", "any openings at", "show me roles at",
    "what PM jobs does [company] have", "what PM jobs were posted this week",
    "search for engineering roles", or any request to browse job listings.

    Company is optional — omit it to search across all ~100 cached companies.
    Filters use case-insensitive substring matching (SQL LIKE), not regex.
    Use commas for OR: title_filter="product manager,PM" matches either.

    Results include last_sync timestamp showing cache freshness, and "already applied"
    markers cross-referenced with the application log. Max 100 results when searching
    all companies (sorted newest first). If cache is empty, tells user to run `jsb sync`.

    Returns the company registry if the company name isn't found."""
    from jobbuddy.settings import get_settings
    from jobbuddy.store import JobStore

    if not get_settings().db_path.exists():
        return "No cached job data. Run `jsb sync` in the terminal to populate the cache."

    # Resolve company
    company_slug = None
    if company:
        resolved = lookup_by_name(company)
        if not resolved:
            companies = list_companies()
            return f"Error: Unknown company '{company}'. Registered companies: {', '.join(c.name for c in companies.values())}"
        company_slug = resolved.slug

    store = JobStore()
    try:
        limit = 100 if not company_slug else 500
        rows = store.query_jobs(
            company=company_slug,
            title=title_filter or None,
            location=location_filter or None,
            limit=limit,
        )

        if not rows:
            filters = []
            if title_filter:
                filters.append(f"title='{title_filter}'")
            if location_filter:
                filters.append(f"location='{location_filter}'")
            filter_desc = f" matching {', '.join(filters)}" if filters else ""
            scope = company_slug or "any company"
            return f"No cached jobs found for {scope}{filter_desc}. Try running `jsb sync` to refresh."

        registry = list_companies()
        log_entries = read_log()

        return JobSearchResults.from_query(rows, registry, log_entries, company_slug=company_slug).to_mcp_result()
    finally:
        store.close()


@mcp.tool
def semantic_search_jobs(
    query: Annotated[str, Field(description="Pass the user's words directly — do not rewrite or summarize")],
    limit: Annotated[int, Field(default=20, description="Max results (default 20)")] = 20,
    model: Annotated[str, Field(default="text3small", description="Embedding model key (ignored, kept for backwards compat)")] = "text3small",
) -> str:
    """Find jobs by meaning rather than keywords. Uses vector similarity over job descriptions.

    Use when the user says "find me jobs like...", describes a role vaguely, or wants
    conceptual matching rather than exact title/location filtering. Complements search_jobs
    (which does keyword matching). Requires descriptions in the cache — only works for
    companies whose ATS returns descriptions during sync (Greenhouse, Ashby, Lever, Rippling, Paylocity).

    Pass the user's natural language query directly — do not rewrite it."""
    from jobbuddy.search import VectorSearch
    from jobbuddy.settings import get_settings

    if not get_settings().db_path.exists():
        return "No cached job data. Run `jsb sync` in the terminal to populate the cache."

    search = VectorSearch()
    try:
        results = search.search(query, limit=limit)

        if not results:
            return "No semantic matches found. Descriptions may not be cached yet — try running `jsb sync` to populate."

        # Convert SearchResult objects to dicts compatible with JobSearchResults.from_query()
        rows = []
        for result in results:
            row = dict(result.job)
            row["distance"] = (1 - result.score) * 2
            rows.append(row)

        registry = list_companies()
        log_entries = read_log()

        return JobSearchResults.from_query(rows, registry, log_entries).to_mcp_result()
    finally:
        search.close()


@mcp.tool
def review_activity_log(
    company: Annotated[str, Field(default="", description="Company name or slug to filter by. Omit for summary of all companies.")] = "",
) -> str:
    """Review job search history — applications, screens, interviews, contacts — for one or all companies.

    Use when the user asks "what have I done with [company]", "show my history with",
    "who have I talked to at", "any contacts at", "what companies should I follow up with",
    "what have I applied to", or "show my application log".

    Without a company: returns a summary of all companies sorted by most recent activity.
    With a company: returns full chronological activity detail for that company."""
    rows = read_log()

    if company:
        # Detail mode: one company
        resolved = lookup_by_name(company)
        display_name = resolved.name if resolved else company

        # Match by display name (case-insensitive)
        company_rows = [r for r in rows if r.get("company", "").lower() == display_name.lower()]

        if not company_rows:
            # Try the raw input as fallback
            company_rows = [r for r in rows if r.get("company", "").lower() == company.lower()]

        if not company_rows:
            return f"No activity found for '{company}'. Check spelling or try review_activity_log() with no args to see all companies."

        ensure_company(display_name)
        return ActivityDetail.from_company(display_name, company_rows).to_mcp_result()

    # Summary mode: all companies
    for name in unique_companies():
        ensure_company(name)

    by_company: dict[str, list[dict]] = {}
    for row in rows:
        co = row.get("company", "").strip()
        if co:
            by_company.setdefault(co, []).append(row)

    return ActivitySummary.from_log(by_company).to_mcp_result()


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("ats://log")
def get_log() -> str:
    """Raw job search activity log as JSON. Prefer review_activity_log tool instead —
    it provides per-company summaries, filtering, and pivot stats. This resource returns
    unprocessed CSV rows."""
    return json.dumps(read_log(), indent=2)


@mcp.resource("ats://companies")
def get_companies() -> str:
    """Registered target companies and their ATS configurations. Use to check which
    companies are available for list_company_jobs and lookup_job."""
    return json.dumps(list_companies(), indent=2)


@mcp.resource("ats://supported-domains")
def get_supported_domains() -> str:
    """URL domain patterns recognized by lookup_job and log_application. Use to decide
    whether a job URL should go to log_application (supported ATS) or log_entry (other)."""
    return json.dumps(SUPPORTED_DOMAINS, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run()


if __name__ == "__main__":
    main()
