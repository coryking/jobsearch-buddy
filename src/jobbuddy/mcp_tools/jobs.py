"""Job search and detail-fetch tools.

`get_job_post_details` fetches full JDs (local first, live for unknown jobs).
`search_jobs` returns the flat ranked list across the whole corpus.
`survey_jobs_by_companies` returns the per-company envelope a watch list
expects.
`get_application_form` fetches the application-form questions for one
posting (Greenhouse / Ashby / Rippling only).
"""

import json
import logging
from typing import Annotated

from pydantic import Field

from jobbuddy.core import (
    CompanyEnvelope,
    JobRow,
    fetch_by_id,
)
from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.mcp_tools.helpers import QUERY_FIELD_DESC, decorate_applied
from jobbuddy.models import Account, CompactJob

log = logging.getLogger(__name__)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
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

    from jobbuddy.registry import lookup_by_name
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


@mcp.tool(annotations={
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
})
def search_jobs(
    query: Annotated[str, Field(default="", description=QUERY_FIELD_DESC)] = "",
    watchlist: Annotated[str, Field(default="", description=(
        "Watchlist slug to scope this search to. The watchlist is a *view* — "
        "its saved companies and filter (query, location_filter, posted_since, "
        "published_since, exclude_companies) are composed with any caller-"
        "passed params: queries AND, locations AND, companies intersect, "
        "exclusions union, date floors take whichever is stricter. List "
        "available watchlists with `watchlist_list`."
    ))] = "",
    exclude_companies: Annotated[list[str], Field(default=[], description="Companies the user has ruled out (e.g. ['microsoft', 'meta']).")] = [],
    location_filter: Annotated[str, Field(default="", description="Where the user wants to work. Substring match on the posting's location field. Comma-separated for OR (e.g. 'seattle,remote').")] = "",
    posted_since: Annotated[str, Field(default="", description="ATS-freshness window — matches publish OR most-recent ATS update. Examples: '24h', '3d', '1w', '2w'. Use this for 'show me what the ATS still considers fresh.' For 'originally posted in this window' (bypasses evergreen-touch bumps), use `published_since`.")] = "",
    published_since: Annotated[str, Field(default="", description="Strict original-publish window — matches `published_at` only, ignoring ATS update-bumps. Use when 'fresh' must mean 'newly created,' not 'evergreen with a recent touch.' Examples: '24h', '3d', '1w', '2w'.")] = "",
    limit: Annotated[int, Field(default=20, ge=1, le=100, description="Max rows to return across the whole corpus. Default 20, hard cap 100.")] = 20,
    account: Account = CurrentAccount(),
) -> list[JobRow]:
    """Search every registered company's job postings as a flat ranked list.

    Use when the user has a concrete role/keyword (and maybe a location or
    recency window) but no specific watch list — \"any rust roles posted
    this week\", \"PM jobs in Seattle\", \"find me staff engineer roles\".
    For a watch-list-scoped scan or per-company breakdown, use
    `survey_jobs_by_companies` instead.

    Pass `watchlist=<slug>` to scope to a saved watchlist. The watchlist is
    treated as a **view, not a default**: its saved filter is composed with
    any caller-passed params (queries AND, locations AND, companies
    intersect, exclusions union, date floors take whichever is stricter).
    Call `watchlist_list` first to see what's saved.

    `posted_since` vs `published_since`: `posted_since` matches ATS-side
    freshness (publish OR last update) — good for \"what the ATS still
    considers fresh.\" `published_since` matches original publish only —
    use when the user wants newly-created roles and evergreen-with-recent-
    touch listings would be misleading.

    Rows are fact-dense (snippet + salary + posted + location inline), so
    do NOT call `get_job_post_details` per row to rank or filter — only
    when the user asks for the full description. Rows the user has already
    logged activity against carry an `applied` summary so you don't surface
    them as fresh leads. Returns an empty list when nothing matches; if
    the registry seems empty, suggest the human run `jsb sync` to refresh."""
    from jobbuddy.core import merge_watchlist_defaults
    from jobbuddy.core import search_jobs as core_search_jobs
    from jobbuddy.core.search import EmptyCompanyIntersectError
    from jobbuddy.store import JobStore

    companies: list[str] | None = None
    if watchlist:
        with JobStore() as store:
            wl = store.get_watchlist(account.id, watchlist)
        if not wl:
            return []  # unknown slug for this account — empty result, not an error
        try:
            (
                query,
                exclude_companies_resolved,
                location_filter,
                posted_since,
                companies,
                published_since,
            ) = merge_watchlist_defaults(
                wl,
                query=query,
                exclude_companies=exclude_companies or None,
                location=location_filter,
                posted_since=posted_since,
                published_since=published_since,
            )
        except EmptyCompanyIntersectError:
            return []  # caller's companies sit outside the watchlist universe
        except ValueError:
            return []  # corrupted filter shape; bail rather than crash the tool
        exclude_companies = exclude_companies_resolved or []

    rows = core_search_jobs(
        query=query,
        companies=companies,
        exclude_companies=exclude_companies or None,
        location=location_filter,
        posted_since=posted_since,
        published_since=published_since,
        limit=limit,
    )
    decorate_applied(rows, account.id)
    return rows


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def get_application_form(
    url: Annotated[str, Field(description=(
        "Job posting URL — the same URL surfaced by search_jobs / "
        "survey_jobs_by_companies rows."
    ))],
) -> str:
    """Fetch the application form (questions an applicant must answer) for one
    posting, when you want to warn the user about surprise questions before
    they click Apply ("this one wants a 1000-word essay", "this one asks for
    references up-front").

    Returns the raw vendor payload as JSON — Greenhouse, Ashby, and Rippling
    are supported. Other ATSes return a short "unsupported" message; the
    user will need to open Apply on the posting to see the form. Read-only;
    does not submit anything."""
    from jobbuddy.fetchers import create_fetcher
    from jobbuddy.fetchers.base import ApplicationFormNotSupported
    from jobbuddy.url import parse_url

    parsed = parse_url(url)
    if parsed is None:
        return (
            f"Could not recognize this URL as a supported job-board posting: {url}. "
            "Pass the canonical posting URL surfaced by search_jobs or "
            "survey_jobs_by_companies."
        )

    try:
        fetcher = create_fetcher(parsed.ats, board=parsed.board)
    except ValueError as e:
        return f"Error: {e}"

    try:
        payload = fetcher.fetch_application_form(parsed.board, parsed.job_id)
    except ApplicationFormNotSupported:
        return (
            f"This ATS ({parsed.ats}) does not expose its application form "
            f"anonymously. The user will need to click 'Apply' on {url} to "
            "see the form."
        )
    except Exception as e:
        log.exception("get_application_form failed for %s", url)
        return f"Error fetching application form for {url}: {e}"

    return json.dumps(payload, indent=2, default=str)


@mcp.tool(annotations={
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
})
def survey_jobs_by_companies(
    companies: Annotated[list[str], Field(min_length=1, description="The watch list to scan: company slugs or display names (e.g. ['anthropic', 'stripe']). Required, must be non-empty. To drill into one company, pass a single-element list.")],
    query: Annotated[str, Field(default="", description=QUERY_FIELD_DESC)] = "",
    exclude_companies: Annotated[list[str], Field(default=[], description="Companies the user has ruled out (e.g. ['microsoft', 'meta']).")] = [],
    location_filter: Annotated[str, Field(default="", description="Where the user wants to work. Substring match on the posting's location field. Comma-separated for OR (e.g. 'seattle,remote').")] = "",
    posted_since: Annotated[str, Field(default="", description="ATS-freshness window — matches publish OR most-recent ATS update. Examples: '24h', '3d', '1w', '2w'. Use this for 'show me what the ATS still considers fresh.' For 'originally posted in this window' (bypasses evergreen-touch bumps), use `published_since`.")] = "",
    published_since: Annotated[str, Field(default="", description="Strict original-publish window — matches `published_at` only, ignoring ATS update-bumps. Use when 'fresh' must mean 'newly created,' not 'evergreen with a recent touch.' Examples: '24h', '3d', '1w', '2w'.")] = "",
    top_per_company: Annotated[int, Field(default=20, ge=1, le=100, description="Max top rows returned per company in the envelope's `top`. Default 20, hard cap 100. Per-company `matches` is always the full count regardless of this cap.")] = 20,
    account: Account = CurrentAccount(),
) -> dict[str, CompanyEnvelope]:
    """Scan a watch list of companies and return a per-company envelope.

    Each entry has `matches` (full count under all filters), optional
    `matches_without_date` (only present when `posted_since` was set), and
    `top` (up to `top_per_company` ranked rows). Zero-match companies stay
    in the result with `matches: 0, top: []` — distinguishing \"no jobs
    here\" from \"capped out.\"

    Use when the user has a specific watch list (\"anything new at my
    saved companies?\"), or when drilling into one company
    (`companies=[<one slug>]` — same shape, single entry). For open-ended
    keyword search across every registered company, use `search_jobs`.

    Read the per-company `matches` as a diagnostic. Wildly varying counts
    across companies in the same vertical usually mean the query is
    hitting substrate words (the technology) rather than role/seniority —
    narrow and re-survey. A large `matches_without_date / matches` ratio
    means the date filter dropped candidates worth widening to.

    Rows the user has already logged activity against carry an `applied`
    summary. If everything comes back zero, suggest the human run `jsb
    sync` to refresh the local store."""
    from jobbuddy.core import survey_jobs_by_companies as core_survey

    envelope = core_survey(
        companies=companies,
        query=query,
        exclude_companies=exclude_companies or None,
        location=location_filter,
        posted_since=posted_since,
        published_since=published_since,
        top_per_company=top_per_company,
    )
    for entry in envelope.values():
        decorate_applied(entry.top, account.id)
    return envelope
