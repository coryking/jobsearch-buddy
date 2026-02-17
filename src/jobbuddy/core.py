"""Shared core logic for ATS operations — used by both CLI and MCP server.

Raises ValueError on errors instead of SystemExit so callers can handle them.
No Rich/Typer dependencies.
"""

import logging
import re
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

from jobbuddy.fetchers import SUPPORTED_ATS_TYPES, create_fetcher, get_fetcher
from jobbuddy.models import Company, FetchResult, Job, slugify
from jobbuddy.registry import list_companies, lookup_by_board, lookup_by_name, lookup_by_slug, register_company
from jobbuddy.url import parse_url


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
            f"Use 'ats list-jobs' to browse jobs, or provide a specific job detail URL "
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

    pub_date = (
        job.published_at[:10]
        if job.published_at and re.match(r"\d{4}-\d{2}-\d{2}", job.published_at)
        else date.today().isoformat()
    )
    filename = f"{pub_date}_{slugify(job.title)}_{job.id}.md"
    filepath = output_dir / filename
    filepath.write_text(job_to_markdown(job))
    return filepath


def result_to_dict(result: FetchResult) -> dict:
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
