"""Flat and per-company job search, plus watchlist default merging.

Both `search_jobs` and `survey_jobs_by_companies` open and close their own
`JobStore` connection — callers (CLI, MCP) treat them as one-shot.
"""

import re

from jobbuddy.core.companies import resolve_company_slugs
from jobbuddy.core.durations import parse_duration_to_date
from jobbuddy.core.types import CompanyEnvelope, JobRow, to_jobrow

_ATS_PUNCT_RE = re.compile(r"[^a-z0-9]")


def normalize_ats_filter(raw: list[str] | None) -> list[str]:
    """Lowercase, strip whitespace, and remove non-alphanumeric characters.

    `Greenhouse`, `green-house`, `GREEN HOUSE` all collapse to `greenhouse`.
    The same normalization is applied to the DB-side `companies.ats` values
    at compare time, so compound values like `oracle_hcm` collapse to
    `oraclehcm` on both sides — letting `oracle_hcm`, `oracle-hcm`, and
    `Oracle HCM` all match the same canonical key.

    Empty / whitespace-only / punctuation-only entries are dropped. Empty
    list or None returns an empty list.
    """
    if not raw:
        return []
    out: list[str] = []
    for s in raw:
        if not s:
            continue
        clean = _ATS_PUNCT_RE.sub("", s.lower())
        if clean:
            out.append(clean)
    return out

WATCHLIST_FILTER_KEYS = {
    "query",
    "location_filter",
    "posted_since",
    "published_since",
    "exclude_companies",
}


class EmptyCompanyIntersectError(ValueError):
    """Composing caller `companies` with watchlist `companies` produced an
    empty intersection — caller is asking for companies that are not in the
    watchlist's universe. Surface as a clear empty-state, not silent zero.
    """


def _stricter_duration(a: str, b: str) -> str:
    """Pick whichever of two duration strings yields the more recent cutoff.

    Both inputs are expected to parse via `parse_duration_to_date` (raise
    ValueError otherwise). The stricter one (more recent ISO date) wins.
    """
    da = parse_duration_to_date(a)
    db = parse_duration_to_date(b)
    return a if da >= db else b


def merge_watchlist_defaults(
    watchlist: dict,
    *,
    query: str,
    exclude_companies: list[str] | None,
    location: str,
    posted_since: str,
    companies: list[str] | None = None,
    published_since: str = "",
) -> tuple[str, list[str] | None, str, str, list[str] | None, str]:
    """Compose a watchlist's saved filter with caller-passed arguments.

    The watchlist's saved filter is the **view**, not a default — caller
    filters are applied *on top* of the watchlist's view, narrowing it
    further. Composition rules per field:

    - `query`: AND-stack. Both watchlist and caller queries apply
      simultaneously; the composed FTS expression is `(A) AND (B)` and is
      handed to `websearch_to_tsquery` which composes correctly.
    - `location_filter`: AND-stack via string concat with ", " — both
      substring lists must match.
    - `companies`: intersect. Caller can only narrow within the
      watchlist's saved companies. Empty intersect raises
      `EmptyCompanyIntersectError`.
    - `exclude_companies`: union — both exclusion lists apply.
    - `posted_since`: AND — stricter (more recent) cutoff wins.
    - `published_since`: AND — stricter (more recent) cutoff wins.

    Returns the resolved tuple
    `(query, exclude_companies, location, posted_since, companies,
    published_since)`.
    """
    f = watchlist.get("filter") or {}
    unknown = set(f) - WATCHLIST_FILTER_KEYS
    if unknown:
        raise ValueError(
            f"watchlist filter has unknown keys: {sorted(unknown)}; "
            f"allowed: {sorted(WATCHLIST_FILTER_KEYS)}"
        )

    wl_query = f.get("query") or ""
    wl_location = f.get("location_filter") or ""
    wl_posted_since = f.get("posted_since") or ""
    wl_published_since = f.get("published_since") or ""
    wl_exclude = f.get("exclude_companies") or []
    wl_companies = watchlist.get("companies") or []

    # query: AND-stack
    if query and wl_query:
        resolved_query = f"({wl_query}) AND ({query})"
    else:
        resolved_query = query or wl_query

    # location: AND-stack (both substring lists must match — implemented at
    # store level as multiple location predicates joined by AND when the
    # composed string contains a sentinel; here we just concatenate with
    # `&` separator and rely on the store to AND-split). Simpler: pass both
    # as a single location string with caller's narrowing semantics — we
    # represent AND by joining with a separator the store treats as AND.
    # The store's existing parsing splits on commas for OR; to AND we keep
    # the watchlist and caller as separate filter terms by joining with a
    # literal "&&" token that the store recognizes. For now, when both
    # have values we apply them as caller-narrows-watchlist using
    # store-side support added below.
    if location and wl_location:
        # Sentinel-joined; store parses "<a>&&<b>" as two AND-matched
        # substring groups (each comma-split for OR).
        resolved_location = f"{wl_location}&&{location}"
    else:
        resolved_location = location or wl_location

    # posted_since: AND (stricter wins)
    if posted_since and wl_posted_since:
        resolved_posted_since = _stricter_duration(posted_since, wl_posted_since)
    else:
        resolved_posted_since = posted_since or wl_posted_since

    # published_since: AND (stricter wins)
    if published_since and wl_published_since:
        resolved_published_since = _stricter_duration(published_since, wl_published_since)
    else:
        resolved_published_since = published_since or wl_published_since

    # exclude_companies: union
    caller_exclude = exclude_companies or []
    union: list[str] = list(dict.fromkeys([*wl_exclude, *caller_exclude]))
    resolved_exclude = union or None

    # companies: intersect (caller narrows within watchlist universe)
    if companies and wl_companies:
        wl_set = set(wl_companies)
        intersect = [c for c in companies if c in wl_set]
        if not intersect:
            raise EmptyCompanyIntersectError(
                f"caller companies {sorted(set(companies))} have no overlap "
                f"with watchlist companies {sorted(wl_set)}"
            )
        resolved_companies: list[str] | None = intersect
    elif companies:
        resolved_companies = companies
    elif wl_companies:
        resolved_companies = list(wl_companies)
    else:
        resolved_companies = None

    return (
        resolved_query,
        resolved_exclude,
        resolved_location,
        resolved_posted_since,
        resolved_companies,
        resolved_published_since,
    )


def resolve_ats_filter(store, raw: list[str] | None) -> list[str] | None:
    """Translate a user `ats` filter to the matching DB-side `companies.ats`
    values. None / empty input returns None (no filter).

    Strategy: normalize both the user's input and the distinct
    `companies.ats` values, then return the subset of DB values whose
    normalized form appears in the user's normalized set. Unknown
    user inputs silently drop — if every value is unknown the result is
    an empty list (which the caller treats as "no companies match,
    return empty"), distinguishing it from the None "filter is off."
    """
    normalized_input = normalize_ats_filter(raw)
    if not normalized_input:
        return None

    rows = store.conn.execute(
        "SELECT DISTINCT ats FROM companies WHERE ats IS NOT NULL"
    ).fetchall()
    wanted = set(normalized_input)
    matched: list[str] = []
    for row in rows:
        actual = row["ats"]
        if _ATS_PUNCT_RE.sub("", actual.lower()) in wanted:
            matched.append(actual)
    return matched


def search_jobs(
    *,
    query: str = "",
    companies: list[str] | None = None,
    exclude_companies: list[str] | None = None,
    location: str = "",
    posted_since: str = "",
    published_since: str = "",
    ats: list[str] | None = None,
    limit: int = 20,
) -> list[JobRow]:
    """Flat ranked job search across the whole stored corpus.

    Returns rows ranked by Postgres FTS when `query` is set, or by
    `effective_date DESC` when it is not. Optionally scope to a specific
    `companies` list — useful when called via a watchlist. Use
    `survey_jobs_by_companies` for a per-company envelope keyed by slug.

    `posted_since` filters on `effective_date = COALESCE(last_listing_update,
    published_at)` — listings the ATS still considers fresh. `published_since`
    filters on `published_at` only — bypasses ATS refresh-bumps. Both can be
    set independently; both AND when both are set.

    Raises ValueError on an unknown company or a bad duration.
    """
    from jobbuddy.store import JobStore

    company_slugs = resolve_company_slugs(companies) if companies else None
    exclude_slugs = resolve_company_slugs(exclude_companies) if exclude_companies else None
    posted_after = parse_duration_to_date(posted_since) if posted_since else None
    published_after = parse_duration_to_date(published_since) if published_since else None

    store = JobStore()
    try:
        ats_actual = resolve_ats_filter(store, ats)
        # User passed an ats filter but no DB values matched → empty result.
        if ats is not None and ats_actual == []:
            return []
        rows = store.search_jobs_fts(
            query=query or None,
            companies=company_slugs,
            exclude_companies=exclude_slugs,
            location=location or None,
            posted_after=posted_after,
            published_after=published_after,
            ats=ats_actual,
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
    published_since: str = "",
    ats: list[str] | None = None,
    top_per_company: int = 20,
) -> dict[str, CompanyEnvelope]:
    """Per-company envelope of matches for a watch list.

    Returns a dict keyed by company slug. Each entry carries `matches`
    (count under all filters), optional `matches_without_date` (only when
    `posted_since` or `published_since` was set), and `top` (up to
    `top_per_company` JobRows). Zero-match companies are first-class entries —
    distinguishing "no jobs match this company" from a silently-applied cap.

    Raises ValueError when `companies` is empty, when any company is
    unknown, or when `posted_since` / `published_since` is malformed.
    """
    if not companies:
        raise ValueError("survey_jobs_by_companies requires a non-empty companies list")

    from jobbuddy.store import JobStore

    company_slugs = resolve_company_slugs(companies)
    exclude_slugs = resolve_company_slugs(exclude_companies) if exclude_companies else None
    posted_after = parse_duration_to_date(posted_since) if posted_since else None
    published_after = parse_duration_to_date(published_since) if published_since else None

    store = JobStore()
    try:
        ats_actual = resolve_ats_filter(store, ats)
        if ats is not None:
            if ats_actual == []:
                return {}
            # Intersect caller's companies with those matching the ATS
            # filter — companies in the caller list whose ATS is filtered
            # out drop from the envelope entirely.
            keep = set(
                row["slug"] for row in store.conn.execute(
                    "SELECT slug FROM companies WHERE ats = ANY(%s)",
                    (ats_actual,),
                ).fetchall()
            )
            company_slugs = [s for s in company_slugs if s in keep]
            if not company_slugs:
                return {}
        raw = store.survey_jobs_by_company(
            companies=company_slugs,
            query=query or None,
            exclude_companies=exclude_slugs,
            location=location or None,
            posted_after=posted_after,
            published_after=published_after,
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
