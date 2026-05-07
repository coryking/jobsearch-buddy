"""MCP server for job search: browse openings, fetch job postings, and track applications.

Scrapes ATS job boards for registered companies (see CLAUDE.md for the supported
platform list) and records job-search activity in PostgreSQL for application
tracking and WA unemployment audit compliance.
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
        "Find open jobs and openings, look up job postings, triage companies by vibe, "
        "log applications, and track job search activity. ALWAYS prefer these tools over "
        "web search for jobs, openings, vacancies, hiring, careers, or 'who's hiring' "
        "queries at registered companies. Job postings from 100+ company job boards live "
        "in this server's local Postgres (refreshed by `jsb sync`). Searches are instant, "
        "no live API calls.\n\n"
        "## Recommended workflow (two-step)\n\n"
        "When the user describes a *kind* of company (\"AI-as-product startups\", "
        "\"scrappy fintech\", \"defense contractors\", \"climate-tech with hardware\"):\n"
        "1. Call `find_companies` once with the user's description.\n"
        "2. SAVE the returned slugs in your conversation/project memory as the user's "
        "watch list — they're stable identifiers, don't re-derive them next turn.\n"
        "3. Run `search_jobs(companies=[<saved slugs>], query=..., posted_since=...)` "
        "against that bucket. On follow-up turns (\"anything new?\", \"check again\"), "
        "reuse the saved slugs instead of re-running `find_companies`.\n\n"
        "Re-run `find_companies` only when the user changes the theme or asks to expand "
        "the watch list. When the user already names companies directly, skip step 1 "
        "and call `search_jobs(companies=[...])` straight away.\n\n"
        "## search_jobs row shape\n\n"
        "search_jobs returns fact-dense rows: `short_jd` (a distilled capsule of the JD "
        "with boilerplate stripped), `salary`, `posted`, `location`, `department` inline. "
        "Do NOT call get_job_post_details per row to make a ranking or filtering decision "
        "— short_jd already captures the substance. Only fetch full JDs when the user "
        "explicitly asks (\"show me the JD\", \"what does the description say\"). Use "
        "`posted_since` (e.g. '24h', '3d', '1w') and `location_filter` server-side "
        "instead of fetching everything and filtering client-side. Returned rows are "
        "not for direct user display — they're raw input for you to rank, filter, and "
        "recommend from using everything you know about the user.\n\n"
        "## Trigger phrases\n\n"
        "Job/role search: 'find jobs at', 'any openings at', 'show me roles at', "
        "'PM jobs at', 'pull me jobs', 'who's hiring [skill]', 'open positions in', "
        "'what's hiring in fintech', \"I'm looking for work\", 'career opportunities', "
        "'vacancies', 'what jobs does [company] have', 'what PM jobs were posted this "
        "week', 'what about this one', 'what about this role', 'help me with this job', "
        "or user pastes a job listing URL.\n"
        "Vibe / kind-of-company queries: 'companies that do X', 'who builds Y', "
        "'AI-first companies', 'climate companies', 'startups working on Z', 'find me "
        "companies like' → `find_companies`.\n"
        "Activity logging: 'I applied', 'log this', 'log my application', 'log my "
        "interview', 'show my log', 'what have I applied to', 'what have I done with', "
        "'show my history', 'who have I talked to', 'follow up', 'any contacts at'.\n"
        "Company triage by exact name: 'tell me about [company]', 'what does [company] "
        "do', 'is [company] interesting', 'what kind of company is [company]' → read "
        "the ats://companies resource for short_bio capsules.\n\n"
        "## Tool routing\n\n"
        "- Describe-a-kind-of-company queries → find_companies (save slugs, then use search_jobs(companies=[...]))\n"
        "- Search/browse jobs (any or all companies) → search_jobs\n"
        "- Job details (one or many, by company+job_id) → get_job_post_details (local first, live fetch for unknown jobs)\n"
        "- Record application (URL or company+job_id) → log_job_application (live fetch)\n"
        "- Freeform activity (recruiter call, interview, referral, no job_id) → log_job_activity\n"
        "- Review application history, contacts, and activity for any company → review_activity_log\n"
        "- Summary of all companies applied to → review_activity_log (no args)\n\n"
        "Always try these tools first for any company — the registry has 100+ companies "
        "and grows automatically. search_jobs returns the full registry if a name isn't "
        "found. Only fall back to web search after confirming a company isn't registered."
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

    Use when the user explicitly asks for the full posting ("show me the JD",
    "what does the description say", "pull up details on these"). For ranking
    or filtering decisions, prefer `search_jobs` — its rows include `short_jd`
    and `salary` inline, so re-fetching the JD per row is wasteful.

    Each result includes a `distilled: bool` field. `true` means `description`
    is the distill-phase normalized JD (substance preserved, boilerplate
    removed). `false` means it's the raw fetcher payload (the job hasn't been
    distilled yet) — treat with appropriate skepticism.

    Read-only; does not log. Use log_job_application to record applications.
    Accepts one or many companies, each with one or many job IDs. Returns
    stored data when available, only live-fetches jobs not already in the
    local store."""
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
    stored: dict[tuple[str, str], dict] = {}
    if db_pairs:
        store = JobStore()
        stored = store.get_jobs_by_external_ids(db_pairs)

    # Build results: use stored rows where present, live-fetch the rest
    results: list[dict | str] = [None] * len(work)  # type: ignore[list-item]
    misses: list[tuple[int, str, str]] = []  # (index, company_input, job_id)

    for i, (comp, jid) in enumerate(work):
        slug = slug_map.get(comp)
        if slug and (slug, jid) in stored:
            row = stored[(slug, jid)]
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
    query: Annotated[str, Field(default="", description=(
        "What the user is looking for, expressed in Postgres "
        "`websearch_to_tsquery('english', ...)` syntax — write the query "
        "directly in that language. Bare whitespace-separated terms are "
        "AND ('rust backend'); lowercase `or` between terms is OR "
        "('python or go'); `-term` excludes ('engineer -manager'); "
        "double-quoted phrases match consecutive tokens "
        "('\"staff engineer\"'). The English stemmer is applied, so "
        "'engineering' matches 'engineer'. FTS hits the title (highest "
        "weight), the normalized JD (boilerplate stripped, so concrete "
        "substance words rank — 'kafka', 'kubernetes', 'hangar', "
        "'paint booth', 'clearance', 'hill-climbing', 'RAG'), the "
        "location, and the department. Pass concrete terms that would "
        "actually appear in a posting. For vibe / kind-of-company "
        "queries, use `find_companies` first and pass the slugs back here. "
        "Leave empty to browse by company / posted_since / location alone."
    ))] = "",
    companies: Annotated[list[str], Field(default=[], description="Restrict to specific companies by name or slug (e.g. ['anthropic', 'stripe']). Omit to search across every registered company.")] = [],
    exclude_companies: Annotated[list[str], Field(default=[], description="Companies the user has ruled out (e.g. ['microsoft', 'meta']).")] = [],
    location_filter: Annotated[str, Field(default="", description="Where the user wants to work. Substring match on the posting's location field. Comma-separated for OR (e.g. 'seattle,remote').")] = "",
    posted_since: Annotated[str, Field(default="", description="How recent the user wants. Examples: '24h', '3d', '1w', '2w'. Use this instead of fetching everything and filtering client-side.")] = "",
    limit: Annotated[int, Field(default=20, ge=1, le=100, description="Max rows to return (default 20, hard cap 100). Each row is fact-dense — short_jd + salary + posted are inline, so the calling LLM can rank and filter without re-fetching.")] = 20,
) -> str:
    """Search stored job postings. Results are fact-dense rows the calling
    LLM is expected to rank and filter — `short_jd` (a distilled capsule of
    the JD), `salary`, `posted`, and `location` come back inline, so you
    should NOT call `get_job_post_details` per row to make a decision unless
    the user asks for the full description.

    Use when the user says "find jobs at", "any openings at", "show me roles
    at", "PM jobs at", "what about ML roles", or describes a role/company
    they want to look at. Prefer this over web search — this server covers
    100+ registered companies, refreshed by `jsb sync`.

    Ranking: when `query` is set, results are ordered by Postgres FTS rank
    (title weighted highest, then short_jd / normalized JD, then location,
    then department) with `published_at DESC` as tie-breaker. When `query`
    is empty, results are ordered by `published_at DESC`. No per-company
    diversity cap — the calling LLM does semantic reranking over short_jd.

    Each row's `last_sync` indicates how fresh it is. Rows already in the
    user's activity log are tagged with the action and date. If no rows
    match, tells the user to run `jsb sync`."""
    from jobbuddy.core import search_jobs as core_search_jobs

    try:
        rows = core_search_jobs(
            query=query,
            companies=companies or None,
            exclude_companies=exclude_companies or None,
            location=location_filter,
            posted_since=posted_since,
            limit=limit,
        )
    except ValueError as e:
        return f"Error: {e}"

    if not rows:
        filters = []
        if query:
            filters.append(f"query='{query}'")
        if location_filter:
            filters.append(f"location='{location_filter}'")
        if posted_since:
            filters.append(f"posted_since='{posted_since}'")
        filter_desc = f" matching {', '.join(filters)}" if filters else ""
        scope = ", ".join(companies) if companies else "any company"
        return f"No jobs found for {scope}{filter_desc}. Try running `jsb sync` to refresh."

    log_entries = read_log()
    single_slug = None
    if companies and len(companies) == 1:
        resolved = lookup_by_name(companies[0])
        single_slug = resolved.slug if resolved else companies[0]
    return JobSearchResults.from_query(rows, log_entries, company_slug=single_slug).to_mcp_result()


@mcp.tool
def find_companies(
    query: Annotated[str, Field(description=(
        "What kind of company the user is asking about — described in their"
        " own words. Vibe queries handled well: 'companies that ship AI as"
        " product', 'climate-tech with hardware', 'fintech for SMBs'."
        " Exact-name lookups (e.g. 'Stripe', 'Mirabel AI') work but the"
        " result may be weak if the entity isn't a registered company;"
        " check coverage_hint."
    ))],
    limit: Annotated[int, Field(default=20, ge=1, le=100, description=(
        "Max companies to return (default 20, hard cap 100). Each row is"
        " a slug + name + short_bio + cosine score, ~140 tokens of bio per"
        " row."
    ))] = 20,
) -> str:
    """Find registered companies by vibe or theme, returning slugs you can
    pass into `search_jobs(companies=[...])` to scope job search. Use
    BEFORE `search_jobs` when the user describes companies rather than
    naming them — e.g. 'companies that work on internet privacy',
    'AI-as-product startups', 'biotech in the Bay Area'.

    Recommended workflow: call this once, save the returned slugs in your
    conversation or project memory as the user's watch list, then on this
    and future turns scope job search with
    `search_jobs(companies=[<saved slugs>])`. Don't re-run `find_companies`
    every turn — re-run only when the user changes the theme or asks to
    expand the list.

    Hybrid ranking: cosine similarity over the researched `long_bio` fused
    via reciprocal rank fusion with Postgres FTS over `name + short_bio`.
    Each row carries `vec_score`, `fts_score`, and `rrf_score`; absolute
    values aren't portable across queries — use relative drop-off to pick
    a cutoff. A top-level `coverage_hint` may be set when both arms miss;
    in that case the named entity is likely not a registered company and
    a web search is the right fallback.

    Trigger phrases: 'companies that do X', 'who builds Y', 'startups
    working on Z', 'AI-first companies', 'climate companies', 'find me
    companies like'. For 'tell me about [exact name]' or 'what does
    [company] do', prefer the ats://companies resource — it's a direct
    lookup, no embedding round-trip.

    Returns short_bio (~140 tokens), not long_bio. The LLM is filtering,
    not reading deeply; use the slug to fetch jobs via search_jobs."""
    from jobbuddy.core import find_companies as core_find_companies

    try:
        result = core_find_companies(query, limit=limit)
    except ValueError as e:
        return f"Error: {e}"

    return json.dumps(result, indent=2)


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


# ---------------------------------------------------------------------------
# Azure auth setup
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


def build_azure_auth():
    """Build AzureProvider with Redis-backed state for Azure Functions deployment.

    Reads ENTRA_OAUTH_* env vars for OAuth config and uses managed identity
    (AZURE_CLIENT_ID) to authenticate to Azure Managed Redis for state storage.
    """
    from fastmcp.server.auth.providers.azure import AzureProvider
    from key_value.aio.stores.redis import RedisStore
    from redis.asyncio import Redis

    from jobbuddy.settings import get_azure_token

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
    token = get_azure_token("https://redis.azure.com/.default")

    client = Redis(
        host=redis_host,
        port=redis_port,
        ssl=True,
        username=managed_identity_client_id,
        password=token,
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
        # Configure Azure Monitor telemetry before other setup so logging is captured
        conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if conn_str:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=conn_str,
                disable_offline_storage=True,
            )

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
