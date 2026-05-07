"""Shared core logic for ATS operations — used by both CLI and MCP server.

Raises ValueError on errors instead of SystemExit so callers can handle them.
No Rich/Typer dependencies.
"""

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

log = logging.getLogger(__name__)

from jobbuddy.fetchers import SUPPORTED_ATS_TYPES, create_fetcher, get_fetcher
from jobbuddy.models import Company, FetchResult, Job, slugify
from jobbuddy.registry import list_companies, lookup_by_board, lookup_by_name, lookup_by_slug, register_company
from jobbuddy.url import parse_url


class JobRow(BaseModel):
    """One ranked job row returned by search_jobs / survey_jobs_by_companies.

    `posted` is an ISO date string when known; `snippet` is a ts_headline
    excerpt (when query is set) or a passive prefix of `short_jd` (when not).
    """

    job_id: str
    title: str
    location: str | None = None
    posted: str | None = None
    snippet: str | None = None
    url: str | None = None
    salary: str | None = None
    company_slug: str
    company_name: str | None = None
    # Populated by MCP wrappers from the per-account activity log; None when
    # the user has not logged anything against this row.
    applied: str | None = None


class CompanyEnvelope(BaseModel):
    """Per-company match envelope for survey_jobs_by_companies.

    `matches` is the count under the active filter set. `matches_without_date`
    is populated only when `posted_since` was set (lets the caller see how
    many candidates the date filter dropped). `top` is the top-N rows.
    """

    matches: int
    matches_without_date: int | None = None
    top: list[JobRow]


def _to_jobrow(row: dict) -> JobRow:
    """Convert a store row to a typed JobRow.

    Accepts both raw rows from `search_jobs_fts` (carrying `published_at`
    and `short_jd`) and pre-normalized rows from `survey_jobs_by_company`
    (carrying `posted` and `snippet`). Falls back to the raw fields when
    the normalized ones are absent so the flat path also gets a passive
    snippet (first ~25 words of `short_jd`).
    """
    posted = row.get("posted")
    if posted is None:
        pa = row.get("published_at")
        posted = pa.isoformat() if hasattr(pa, "isoformat") else (pa or None)

    snippet = row.get("snippet")
    if snippet is None:
        short_jd = row.get("short_jd")
        if short_jd:
            words = short_jd.split()
            if words:
                snippet = " ".join(words[:25])

    return JobRow(
        job_id=row["job_id"],
        title=row["title"],
        location=row.get("location") or None,
        posted=posted,
        snippet=snippet,
        url=row.get("url") or None,
        salary=row.get("salary"),
        company_slug=row["company_slug"],
        company_name=row.get("company_name") or row["company_slug"],
    )


def parse_duration_to_date(value: str) -> str:
    """Parse a human-friendly duration into an ISO date string (YYYY-MM-DD).

    Accepts: 24h, 3d, 1w, 2w, etc.
    Raises ValueError on invalid input.
    """
    m = re.fullmatch(r"(\d+)\s*(h|d|w)", value.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration '{value}'. Use e.g. 24h, 3d, 1w, 2w.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "h":
        delta = timedelta(hours=n)
    elif unit == "d":
        delta = timedelta(days=n)
    else:
        delta = timedelta(weeks=n)
    return (date.today() - delta).isoformat()


def resolve_company_slugs(names: list[str]) -> list[str]:
    """Resolve a list of company names/slugs to canonical slugs.

    Raises ValueError naming the unknown entry. Used by both CLI and MCP so
    the failure mode is consistent.
    """
    slugs = []
    for name in names:
        resolved = lookup_by_name(name)
        if not resolved:
            available = ", ".join(c.name for c in list_companies().values())
            raise ValueError(f"Unknown company '{name}'. Registered: {available}")
        slugs.append(resolved.slug)
    return slugs


def find_companies(
    query: str,
    *,
    limit: int = 20,
    coverage_floor: float = 0.35,
) -> dict:
    """Hybrid search over registered companies — vector + FTS, fused via RRF.

    Returns `{"results": [{slug, name, short_bio, active_jobs}],
    "coverage_hint": str | None}`. `active_jobs` is the count of jobs with
    `listing_status = 'active'` for the company. The MCP tool wrapper
    additionally attaches a per-account `applications` count to each
    result row before serializing — that field is account-scoped and not
    produced by this function.

    Internal vec/fts/rrf scores drive ordering but aren't returned here —
    use `jsb search-debug` to inspect them.

    `coverage_hint` is set when neither arm produced a strong match:
    vector below `coverage_floor` AND no FTS match. That tells the caller
    the named entity may not be a registered company and a web search is
    the right fallback.

    Always returns top-N rows when any embeddings exist; never returns
    empty just because the query is unusual. Raises ValueError when query
    is empty, limit < 1, or no company embeddings exist yet.
    """
    from jobbuddy.embeddings import embed_text
    from jobbuddy.store import JobStore

    if not query or not query.strip():
        raise ValueError("find_companies requires a non-empty query")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    import psycopg

    embedding, _tokens = embed_text(query)

    store = JobStore()
    try:
        rows = store.find_companies(embedding, query, limit=limit)
    except psycopg.Error as e:
        # websearch_to_tsquery is generous but malformed input still raises.
        # Re-raise as ValueError so MCP/CLI handlers can map it cleanly.
        raise ValueError(f"Invalid query for find_companies: {e}") from e
    finally:
        store.close()

    if not rows:
        raise ValueError(
            "No company embeddings yet. Run `jsb research-companies`"
            " to fill bios + embeddings."
        )

    top_vec = max((r["vec_score"] for r in rows if r["vec_score"] is not None), default=None)
    top_fts = max((r["fts_score"] for r in rows if r["fts_score"] is not None), default=None)

    # Coverage hint fires only when *both* arms missed: vector below floor
    # AND no FTS match at all. ts_rank is bounded differently than cosine
    # similarity, and any FTS hit means the query's tokens literally appear
    # in some company's name+short_bio — that's positive evidence the
    # entity is registered, even when the score is small.
    # The FTS CTE's WHERE already guarantees only real matches reach this
    # row, so any non-None fts_score is positive evidence of registration —
    # even ts_rank == 0.0 (which can occur on very short docs).
    coverage_hint = None
    vec_weak = top_vec is None or top_vec < coverage_floor
    fts_missed = top_fts is None
    if vec_weak and fts_missed:
        coverage_hint = (
            "Top match is weak — the named entity may not be a registered"
            " company. For exact-name lookups, fall back to web search."
        )

    def _round(v):
        return None if v is None else round(float(v), 4)

    results = [
        {
            "slug": r["slug"],
            "name": r["name"],
            "short_bio": r["short_bio"],
            "active_jobs": int(r["active_jobs"]),
        }
        for r in rows
    ]

    # Audit log so a few weeks of real traffic answer the open Phase 2
    # questions: is the coverage_floor calibrated correctly, and does the
    # FTS arm earn its keep on real queries (the deferred tuning question
    # from issue #41)? Scores aren't exposed in the response anymore but
    # still belong in logs. The `extra` dict makes structured-log scrapers
    # happy; the formatted message is for grep.
    top_slugs = [r["slug"] for r in results[:3]]
    top_rrf = next(
        (_round(r["rrf_score"]) for r in rows), None
    )
    log.info(
        "find_companies q=%r top_vec=%s top_fts=%s top_slugs=%s "
        "coverage_hint=%s n=%d",
        query, _round(top_vec), _round(top_fts), top_slugs,
        bool(coverage_hint), len(results),
        extra={
            "find_companies_query": query,
            "find_companies_top_vec": _round(top_vec),
            "find_companies_top_fts": _round(top_fts),
            "find_companies_top_rrf": top_rrf,
            "find_companies_top_slugs": top_slugs,
            "find_companies_coverage_hint": bool(coverage_hint),
            "find_companies_result_count": len(results),
        },
    )

    return {
        "results": results,
        "coverage_hint": coverage_hint,
    }


def search_jobs(
    *,
    query: str = "",
    exclude_companies: list[str] | None = None,
    location: str = "",
    posted_since: str = "",
    limit: int = 20,
) -> list[JobRow]:
    """Flat ranked job search across the whole stored corpus.

    Returns rows ranked by Postgres FTS when `query` is set, or by
    `published_at DESC` when it is not. Use `survey_jobs_by_companies` for
    a per-company envelope keyed by slug.

    Raises ValueError on an unknown excluded company or a bad duration.
    """
    from jobbuddy.store import JobStore

    exclude_slugs = resolve_company_slugs(exclude_companies) if exclude_companies else None
    posted_after = parse_duration_to_date(posted_since) if posted_since else None

    store = JobStore()
    try:
        rows = store.search_jobs_fts(
            query=query or None,
            companies=None,
            exclude_companies=exclude_slugs,
            location=location or None,
            posted_after=posted_after,
            limit=limit,
        )
    finally:
        store.close()

    return [_to_jobrow(r) for r in rows]


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
            top=[_to_jobrow(r) for r in entry.get("top", [])],
        )
    return out


def resolve_exclude_companies(exclude_csv: str) -> list[str]:
    """Parse comma-separated company names/slugs into resolved slug list."""
    slugs = []
    for name in exclude_csv.split(","):
        name = name.strip()
        if not name:
            continue
        resolved = lookup_by_name(name)
        slugs.append(resolved.slug if resolved else name)
    return slugs


def fetch_from_url(url: str) -> FetchResult:
    """Parse a URL, resolve/register company, fetch job.

    Raises ValueError if the URL is unrecognized or the ATS is unsupported.
    """
    parsed = parse_url(url)
    if not parsed:
        raise ValueError(
            f"Unrecognized ATS URL: {url}\n"
            "Supported: Greenhouse, Ashby, Lever, Rippling, Workday, Eightfold (Microsoft), Oracle HCM, Paylocity"
        )

    if parsed.ats not in SUPPORTED_ATS_TYPES:
        raise ValueError(f"No fetcher for ATS type: {parsed.ats}")

    # Paylocity listing URLs have board but no job_id — can't fetch a single job
    if parsed.ats == "paylocity" and parsed.board and not parsed.job_id:
        raise ValueError(
            f"This is a Paylocity job listing page, not a single job posting.\n"
            f"Use 'jsb list-jobs' to browse jobs, or provide a specific job detail URL "
            f"(e.g. recruiting.paylocity.com/Recruiting/Jobs/Details/{{id}})."
        )

    # Paylocity detail URLs don't contain the company UUID — resolve it from the page
    if parsed.ats == "paylocity" and not parsed.board and parsed.job_id:
        import httpx
        from urllib.parse import urlparse as _urlparse

        host = _urlparse(url).hostname or ""
        prefix_m = re.match(r"(\d+)recruiting\.paylocity\.com", host)
        ats_prefix = prefix_m.group(1) if prefix_m else ""

        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        # Extract company UUID from the "All Jobs" link in the page
        all_jobs_m = re.search(
            r'/Recruiting/Jobs/All/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/([^"\'<>]+))?',
            resp.text,
            re.IGNORECASE,
        )
        if all_jobs_m:
            parsed.board = all_jobs_m.group(1)
            company_slug = all_jobs_m.group(2) or ""
        else:
            # Fallback: extract moduleName from pageData
            name_m = re.search(r'"moduleName"\s*:\s*"([^"]+)"', resp.text)
            company_slug = ""
            # Can't determine board UUID — create a temporary fetcher with empty board
            display_name = name_m.group(1) if name_m else "Unknown"
            from jobbuddy.fetchers.paylocity import PaylocityFetcher
            temp_fetcher = PaylocityFetcher("", display_name, ats_prefix=ats_prefix)
            job = temp_fetcher._parse_detail_page(resp.text, parsed.job_id, url)
            company = register_company(display_name, "paylocity", "", company_slug=company_slug, ats_prefix=ats_prefix)
            return FetchResult(company=company, job=job)

        # We have the board UUID now — check registry and auto-register if needed
        company = lookup_by_slug(parsed.board) or lookup_by_board(parsed.board, "paylocity")
        if not company or not company.ats:
            # Parse the detail page we already fetched
            page_name_m = re.search(r'"moduleName"\s*:\s*"([^"]+)"', resp.text)
            display_name = page_name_m.group(1) if page_name_m else company_slug.replace("-", " ").title()
            extra = {"company_slug": company_slug}
            if ats_prefix:
                extra["ats_prefix"] = ats_prefix
            company = register_company(display_name, "paylocity", parsed.board, **extra)

        # Parse the job from the already-fetched page
        from jobbuddy.fetchers.paylocity import PaylocityFetcher
        temp_fetcher = PaylocityFetcher(parsed.board, company.name, company_slug=company_slug, ats_prefix=ats_prefix)
        job = temp_fetcher._parse_detail_page(resp.text, parsed.job_id, url)
        return FetchResult(company=company, job=job)

    # Try to find company in registry by slug, then by board value
    company = lookup_by_slug(parsed.board) or lookup_by_board(parsed.board, parsed.ats)
    if not company or not company.ats:
        # Auto-register — try to resolve the real company name from the ATS
        temp = create_fetcher(parsed.ats, board=parsed.board)
        display_name = temp.resolve_name() or parsed.board.replace("-", " ").replace("_", " ").title()
        extra = {}
        if parsed.ats == "workday":
            from urllib.parse import urlparse as _urlparse

            host = _urlparse(url).hostname or ""
            m = re.search(r"([a-z0-9-]+)\.wd(\d+)\.myworkdayjobs\.com", host)
            if m:
                extra["wd_company"] = m.group(1)
                extra["wd_instance"] = int(m.group(2))
        elif parsed.ats == "eightfold":
            from urllib.parse import urlparse as _urlparse

            p = _urlparse(url)
            extra["base_url"] = f"{p.scheme}://{p.hostname}"
            extra["domain"] = (p.hostname or "").replace("apply.careers.", "")
        elif parsed.ats == "oracle_hcm":
            m = re.search(r"\.fa\.([a-z0-9]+)\.oraclecloud\.com", url)
            extra["ohcm_region"] = m.group(1) if m else ""
            m2 = re.search(r"/sites/([^/]+)/", url)
            extra["site_slug"] = m2.group(1) if m2 else "jobsearch"
            extra["site_number"] = "CX_1"
        elif parsed.ats == "paylocity":
            from urllib.parse import urlparse as _urlparse

            host = _urlparse(url).hostname or ""
            prefix_m = re.match(r"(\d+)recruiting\.paylocity\.com", host)
            if prefix_m:
                extra["ats_prefix"] = prefix_m.group(1)
            # Extract company slug from listing URL path
            slug_m = re.search(
                r"/Recruiting/Jobs/All/[0-9a-f-]+/([^/?#]+)", url, re.IGNORECASE
            )
            if slug_m:
                extra["company_slug"] = slug_m.group(1)
        company = register_company(display_name, parsed.ats, parsed.board, **extra)

    fetcher = get_fetcher(company)
    job = fetcher.fetch_job(parsed.job_id)

    return FetchResult(company=company, job=job)


def fetch_by_id(company_input: str, job_id: str) -> FetchResult:
    """Fetch a job by company slug/name + job ID.

    Raises ValueError if the company is unknown or ATS unsupported.
    """
    company = lookup_by_name(company_input)
    if not company:
        companies = list_companies()
        raise ValueError(
            f"Unknown company: {company_input}\n"
            f"Available: {', '.join(companies.keys())}"
        )

    if not company.ats or company.ats not in SUPPORTED_ATS_TYPES:
        raise ValueError(
            f"No job board configured for {company.name}. "
            "Use a job listing URL instead — it auto-detects the ATS type."
        )

    fetcher = get_fetcher(company)
    job = fetcher.fetch_job(job_id)

    return FetchResult(company=company, job=job)


def job_to_markdown(job: Job) -> str:
    """Render a Job as a markdown string."""
    return f"""# {job.title}

## Essentials
- **Job ID:** {job.id}
- **URL:** {job.url}
- **Apply:** {job.apply_url}
- **Location:** {job.location or "Not specified"}
- **Salary:** {job.salary or "Not specified"}
- **Team:** {job.team or job.department or "Not specified"}
- **Posted:** {job.published_at or "Unknown"}

## Description
{job.description or "No description available."}
"""


def save_job_listing(company: Company, job: Job) -> Path:
    """Save a job listing as markdown. Returns the file path."""
    from jobbuddy.settings import get_settings
    output_dir = get_settings().listings_dir / company.slug
    output_dir.mkdir(parents=True, exist_ok=True)

    pub_date = (job.published_at or date.today()).isoformat()
    filename = f"{pub_date}_{slugify(job.title)}_{job.id}.md"
    filepath = output_dir / filename
    filepath.write_text(job_to_markdown(job))
    return filepath


def result_to_dict(result: FetchResult) -> dict[str, Any]:
    """Convert a FetchResult to a JSON-serializable dict with company metadata."""
    output = result.job.model_dump()
    output["_company_slug"] = result.company.slug
    output["_company_name"] = result.company.name
    output["_ats"] = result.company.ats
    return output


def is_supported_ats_url(url: str) -> bool:
    """Check if a URL is from a supported ATS platform."""
    return parse_url(url) is not None


SUPPORTED_DOMAINS = [
    "greenhouse.io (e.g. job-boards.greenhouse.io/<board>/jobs/<id>)",
    "jobs.ashbyhq.com (e.g. jobs.ashbyhq.com/<board>/<uuid>)",
    "jobs.lever.co (e.g. jobs.lever.co/<company>/<uuid>)",
    "*.myworkdayjobs.com (e.g. <company>.wd<N>.myworkdayjobs.com/...)",
    "ats.rippling.com (e.g. ats.rippling.com/<company>/jobs/<uuid>)",
    "apply.careers.microsoft.com (e.g. apply.careers.microsoft.com/careers/job/<id>)",
    "*.fa.*.oraclecloud.com (e.g. {tenant}.fa.{region}.oraclecloud.com/.../job/{id})",
    "recruiting.paylocity.com (e.g. recruiting.paylocity.com/Recruiting/Jobs/Details/{id})",
]
