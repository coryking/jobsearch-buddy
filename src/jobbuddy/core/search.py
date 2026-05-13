"""Flat and per-company job search, plus watchlist default merging.

Both `search_jobs` and `survey_jobs_by_companies` open and close their own
`JobStore` connection — callers (CLI, MCP) treat them as one-shot.
"""

from jobbuddy.core.companies import resolve_company_slugs
from jobbuddy.core.durations import parse_duration_to_date
from jobbuddy.core.types import CompanyEnvelope, JobRow, to_jobrow

WATCHLIST_FILTER_KEYS = {"query", "location_filter", "posted_since", "exclude_companies"}


def merge_watchlist_defaults(
    watchlist: dict,
    *,
    query: str,
    exclude_companies: list[str] | None,
    location: str,
    posted_since: str,
    companies: list[str] | None = None,
) -> tuple[str, list[str] | None, str, str, list[str] | None]:
    """Layer a watchlist's saved defaults under caller-passed arguments.

    Explicit caller args win; the watchlist fills in what the caller left
    empty/None. Returns the resolved (query, exclude_companies, location,
    posted_since, companies) tuple. The `filter` JSONB shape is the same
    keyword set as `search_jobs` minus `limit`.
    """
    f = watchlist.get("filter") or {}
    unknown = set(f) - WATCHLIST_FILTER_KEYS
    if unknown:
        raise ValueError(
            f"watchlist filter has unknown keys: {sorted(unknown)}; "
            f"allowed: {sorted(WATCHLIST_FILTER_KEYS)}"
        )

    resolved_query = query or (f.get("query") or "")
    resolved_location = location or (f.get("location_filter") or "")
    resolved_posted_since = posted_since or (f.get("posted_since") or "")
    resolved_exclude = exclude_companies if exclude_companies else (f.get("exclude_companies") or None)
    resolved_companies = companies if companies else (watchlist.get("companies") or None)
    return resolved_query, resolved_exclude, resolved_location, resolved_posted_since, resolved_companies


def search_jobs(
    *,
    query: str = "",
    companies: list[str] | None = None,
    exclude_companies: list[str] | None = None,
    location: str = "",
    posted_since: str = "",
    limit: int = 20,
) -> list[JobRow]:
    """Flat ranked job search across the whole stored corpus.

    Returns rows ranked by Postgres FTS when `query` is set, or by
    `effective_date DESC` when it is not. Optionally scope to a specific
    `companies` list — useful when called via a watchlist. Use
    `survey_jobs_by_companies` for a per-company envelope keyed by slug.

    Raises ValueError on an unknown company or a bad duration.
    """
    from jobbuddy.store import JobStore

    company_slugs = resolve_company_slugs(companies) if companies else None
    exclude_slugs = resolve_company_slugs(exclude_companies) if exclude_companies else None
    posted_after = parse_duration_to_date(posted_since) if posted_since else None

    store = JobStore()
    try:
        rows = store.search_jobs_fts(
            query=query or None,
            companies=company_slugs,
            exclude_companies=exclude_slugs,
            location=location or None,
            posted_after=posted_after,
            limit=limit,
        )
    finally:
        store.close()

    return [to_jobrow(r) for r in rows]


def survey_jobs_by_companies(
    *,
    companies: list[str],
    query: str = "",
    exclude_companies: list[str] | None = None,
    location: str = "",
    posted_since: str = "",
    top_per_company: int = 20,
) -> dict[str, CompanyEnvelope]:
    """Per-company envelope of matches for a watch list.

    Returns a dict keyed by company slug. Each entry carries `matches`
    (count under all filters), optional `matches_without_date` (only when
    `posted_since` was set), and `top` (up to `top_per_company` JobRows).
    Zero-match companies are first-class entries — distinguishing "no jobs
    match this company" from a silently-applied cap.

    Raises ValueError when `companies` is empty, when any company is
    unknown, or when `posted_since` is malformed.
    """
    if not companies:
        raise ValueError("survey_jobs_by_companies requires a non-empty companies list")

    from jobbuddy.store import JobStore

    company_slugs = resolve_company_slugs(companies)
    exclude_slugs = resolve_company_slugs(exclude_companies) if exclude_companies else None
    posted_after = parse_duration_to_date(posted_since) if posted_since else None

    store = JobStore()
    try:
        raw = store.survey_jobs_by_company(
            companies=company_slugs,
            query=query or None,
            exclude_companies=exclude_slugs,
            location=location or None,
            posted_after=posted_after,
            top_per_company=top_per_company,
        )
    finally:
        store.close()

    out: dict[str, CompanyEnvelope] = {}
    for slug, entry in raw.items():
        out[slug] = CompanyEnvelope(
            matches=entry["matches"],
            matches_without_date=entry.get("matches_without_date"),
            top=[to_jobrow(r) for r in entry.get("top", [])],
        )
    return out
