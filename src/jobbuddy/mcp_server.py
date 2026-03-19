"""MCP server for job search: browse openings, fetch job postings, and track applications.

Scrapes ATS job boards (Greenhouse, Ashby, Lever, Workday, Rippling, Paylocity) for registered
companies and maintains a CSV activity log for application tracking and WA unemployment
audit compliance.
"""

import json
import logging
import os
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
        "- Meaning-based / 'find me jobs like...' / vague descriptions → search_jobs with query param\n"
        "- Job details (one or many, by company+job_id) → get_job_post_details (cached, live fetch fallback)\n"
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
    jobs: Annotated[str, Field(description=(
        'JSON array of companies with job IDs to fetch. '
        'Format: [{"company": "acme", "job_ids": ["123", "456"]}, {"company": "beta", "job_ids": ["789"]}]. '
        'Company can be a slug or name from the registry.'
    ))],
) -> str:
    """Fetch full details of one or more job postings — title, salary, location, description.

    Use when the user shares job IDs from search results or says "tell me about these jobs",
    "what about this one", "pull up details on these". Read-only — does not log.
    Use log_job_application to record applications.

    Accepts one or many companies, each with one or many job IDs. Returns cached data
    from local DB when available, only live-fetches jobs not in the cache."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from jobbuddy.store import JobStore

    try:
        requests = json.loads(jobs)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON in jobs parameter: {e}"

    if not isinstance(requests, list) or not requests:
        return "Error: jobs must be a non-empty JSON array."

    # Build flat list of (company_input, job_id) and resolve slugs
    work: list[tuple[str, str]] = []  # (company_input, job_id)
    slug_map: dict[str, str] = {}     # company_input -> slug
    for entry in requests:
        company = entry.get("company", "")
        job_ids = entry.get("job_ids", [])
        if not company or not job_ids:
            return f"Error: Each entry needs 'company' and 'job_ids'. Got: {entry}"
        # Resolve company name/slug once per entry
        if company not in slug_map:
            resolved = lookup_by_name(company)
            if resolved:
                slug_map[company] = resolved.slug
        for jid in job_ids:
            work.append((company, str(jid)))

    # Try local DB first for all resolved jobs
    db_pairs = [
        (slug_map[comp], jid)
        for comp, jid in work
        if comp in slug_map
    ]
    cached: dict[tuple[str, str], dict] = {}
    if db_pairs:
        store = JobStore()
        cached = store.get_jobs_by_external_ids(db_pairs)

    # Build results: use cache hits, live-fetch misses
    results: list[dict | str] = [None] * len(work)  # type: ignore[list-item]
    misses: list[tuple[int, str, str]] = []  # (index, company_input, job_id)

    for i, (comp, jid) in enumerate(work):
        slug = slug_map.get(comp)
        if slug and (slug, jid) in cached:
            row = cached[(slug, jid)]
            results[i] = CompactJob.from_db_row(row, row.get("company_name") or slug).model_dump()
        else:
            misses.append((i, comp, jid))

    # Live-fetch any misses in parallel
    if misses:
        def fetch_one(company_input: str, job_id: str) -> CompactJob | str:
            try:
                result = fetch_by_id(company_input, job_id)
                return CompactJob.from_result(result)
            except (ValueError, Exception) as e:
                return f"Error fetching {company_input}/{job_id}: {e}"

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(fetch_one, comp, jid): idx
                for idx, comp, jid in misses
            }
            for future in as_completed(futures):
                idx = futures[future]
                result = future.result()
                if isinstance(result, CompactJob):
                    results[idx] = result.model_dump()
                else:
                    results[idx] = {"error": result}

    return json.dumps(results, indent=2, default=str)


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
    since: Annotated[str, Field(default="", description="Only return jobs posted within this period. Examples: '24h' (last 24 hours), '3d' (3 days), '1w' (1 week), '2w' (2 weeks).")] = "",
    query: Annotated[str, Field(default="", description="Natural language query to search job descriptions by meaning (e.g. 'kubernetes security', 'ML infrastructure'). When provided, results are ranked by semantic similarity. Combinable with company/title/location filters.")] = "",
) -> str:
    """Search cached job listings across one or all companies. Data comes from a local SQLite
    cache populated by `jsb sync` — results are instant, no live API calls.

    REQUIRED: You must provide either `title_filter` or `query` (or both). Browsing
    without any search criteria is not supported — the cache has thousands of jobs.

    Use when the user says "find jobs at", "any openings at", "show me roles at",
    "what PM jobs does [company] have", "find me jobs like...", describes a role vaguely,
    or any request to browse or search job listings.

    Without `query`: filters by company/title/location using keyword matching (SQL LIKE).
    With `query`: ranks results by semantic similarity to the query text, optionally
    filtered by company/title/location. Pass the user's words directly as the query.

    If results exceed the max limit, the tool returns an error instead of truncating.
    When this happens, narrow your search by adding filters: location_filter, since,
    title_filter, or company. Do NOT just accept truncated results — refine the query.

    Company is optional — omit it to search across all ~100 cached companies.
    Filters use case-insensitive substring matching (SQL LIKE), not regex.
    Use commas for OR: title_filter="product manager,PM" matches either.

    Results include last_sync timestamp showing cache freshness, and "already applied"
    markers cross-referenced with the application log. If cache is empty, tells user
    to run `jsb sync`.

    Returns the company registry if the company name isn't found."""
    from jobbuddy.search import VectorSearch

    try:
        from jobbuddy.store import JobStore
        test_store = JobStore()
        has_data = test_store.cache_exists()
        test_store.close()
        if not has_data:
            return "No cached job data. Run `jsb sync` in the terminal to populate the cache."
    except Exception:
        return "Cannot connect to database. Check pg_service.conf configuration."

    # Require at least a title or semantic query
    if not title_filter and not query:
        return (
            "Error: You must provide either `title_filter` or `query` (or both). "
            "The cache has thousands of jobs — browsing without search criteria is not supported. "
            "Examples: title_filter='product manager,PM' or query='ML infrastructure roles'."
        )

    # Resolve company
    company_slug = None
    if company:
        resolved = lookup_by_name(company)
        if not resolved:
            companies = list_companies()
            return f"Error: Unknown company '{company}'. Registered companies: {', '.join(c.name for c in companies.values())}"
        company_slug = resolved.slug

    max_results = 100

    search = VectorSearch()
    try:
        # Parse since duration
        posted_after = None
        if since:
            from jobbuddy.core import parse_duration_to_date

            try:
                posted_after = parse_duration_to_date(since)
            except ValueError:
                return f"Error: Invalid 'since' value '{since}'. Use e.g. '24h', '3d', '1w', '2w'."

        # Fetch one extra to detect overflow
        results = search.search(
            query=query or None,
            company=company_slug,
            title=title_filter or None,
            location=location_filter or None,
            posted_after=posted_after,
            limit=max_results + 1,
        )

        if not results:
            filters = []
            if title_filter:
                filters.append(f"title='{title_filter}'")
            if location_filter:
                filters.append(f"location='{location_filter}'")
            if since:
                filters.append(f"since='{since}'")
            if query:
                filters.append(f"query='{query}'")
            filter_desc = f" matching {', '.join(filters)}" if filters else ""
            scope = company_slug or "any company"
            return f"No cached jobs found for {scope}{filter_desc}. Try running `jsb sync` to refresh."

        # Refuse to return silently truncated results
        if len(results) > max_results:
            current_filters = []
            if company_slug:
                current_filters.append(f"company='{company_slug}'")
            if title_filter:
                current_filters.append(f"title_filter='{title_filter}'")
            if location_filter:
                current_filters.append(f"location_filter='{location_filter}'")
            if since:
                current_filters.append(f"since='{since}'")
            if query:
                current_filters.append(f"query='{query}'")
            suggestions = []
            if not location_filter:
                suggestions.append("location_filter (e.g. 'seattle,remote')")
            if not since:
                suggestions.append("since (e.g. '1w', '3d')")
            if not company_slug:
                suggestions.append("company")
            if not title_filter and query:
                suggestions.append("title_filter")
            return (
                f"Too many results (>{max_results}). Your query is too broad — "
                f"narrow it down instead of accepting truncated results.\n"
                f"Current filters: {', '.join(current_filters)}\n"
                f"Try adding: {', '.join(suggestions)}"
            )

        rows = []
        for result in results:
            row = dict(result.job)
            if result.score is not None:
                row["distance"] = 1.0 - result.score
            rows.append(row)

        log_entries = read_log()

        return JobSearchResults.from_query(rows, log_entries, company_slug=company_slug).to_mcp_result()
    finally:
        search.close()


@mcp.tool
def semantic_search_jobs(
    query: Annotated[str, Field(description="Pass the user's words directly — do not rewrite or summarize")],
    limit: Annotated[int, Field(default=20, description="Max results (default 20)")] = 20,
    model: Annotated[str, Field(default="text3small", description="Embedding model key (ignored, kept for backwards compat)")] = "text3small",
) -> str:
    """Deprecated: use search_jobs with the `query` parameter instead.

    Kept for backwards compatibility with existing MCP clients."""
    return search_jobs(query=query)


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
# Azure auth setup
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


def build_azure_auth():
    """Build AzureProvider with Redis-backed state for Azure Functions deployment.

    Reads ENTRA_OAUTH_* env vars for OAuth config and uses managed identity
    (AZURE_CLIENT_ID) to authenticate to Azure Managed Redis for state storage.
    """
    from azure.identity import DefaultAzureCredential
    from fastmcp.server.auth.providers.azure import AzureProvider
    from key_value.aio.stores.redis import RedisStore
    from redis.asyncio import Redis

    oauth_client_id = os.environ["ENTRA_OAUTH_CLIENT_ID"]
    oauth_client_secret = os.environ["ENTRA_OAUTH_CLIENT_SECRET"]
    oauth_tenant_id = os.environ["ENTRA_OAUTH_TENANT_ID"]
    oauth_identifier_uri = os.environ.get("ENTRA_OAUTH_IDENTIFIER_URI")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

    redis_host = os.environ.get("REDIS_HOST", "")
    redis_port = int(os.environ.get("REDIS_PORT", "10000"))
    managed_identity_client_id = os.environ.get("AZURE_CLIENT_ID", "")

    if not redis_host:
        raise RuntimeError("REDIS_HOST not set — cannot initialize OAuth state store")
    if not managed_identity_client_id:
        raise RuntimeError("AZURE_CLIENT_ID not set — cannot authenticate to Redis")

    log.info("Acquiring Entra token for Redis")
    credential = DefaultAzureCredential()
    token = credential.get_token("https://redis.azure.com/.default")

    client = Redis(
        host=redis_host,
        port=redis_port,
        ssl=True,
        username=managed_identity_client_id,
        password=token.token,
        decode_responses=True,
    )

    redis_store = RedisStore(
        client=client,
        default_collection="mcp_oauth_state",
    )

    auth = AzureProvider(
        client_id=oauth_client_id,
        client_secret=oauth_client_secret,
        tenant_id=oauth_tenant_id,
        required_scopes=["user_impersonation"],
        base_url=base_url,
        identifier_uri=oauth_identifier_uri or None,
        client_storage=redis_store,
    )

    log.info("AzureProvider initialized with RedisStore (host=%s)", redis_host)
    return auth


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if os.environ.get("ENTRA_OAUTH_CLIENT_ID"):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        auth = build_azure_auth()
        mcp.auth = auth
        mcp.run(transport="streamable-http", stateless_http=True)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
