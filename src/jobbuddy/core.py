"""Shared core logic for ATS operations — used by both CLI and MCP server.

Raises ValueError on errors instead of SystemExit so callers can handle them.
No Rich/Typer dependencies.
"""

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from time import monotonic

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


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class SyncResult:
    """Result from syncing a single company."""

    __slots__ = ("slug", "job_count", "error", "elapsed")

    def __init__(self, slug: str, job_count: int = 0, error: str | None = None, elapsed: float = 0.0):
        self.slug = slug
        self.job_count = job_count
        self.error = error
        self.elapsed = elapsed

    @property
    def ok(self) -> bool:
        return self.error is None


def _fetch_company_jobs(company: Company, on_start: "callable | None" = None) -> tuple[str, list[Job] | str]:
    """Worker function: fetch jobs for a company. Returns (slug, jobs) or (slug, error_string)."""
    if on_start:
        on_start(company.slug)
    try:
        fetcher = get_fetcher(company)
        jobs = fetcher.list_jobs()
        return (company.slug, jobs)
    except Exception as e:
        return (company.slug, str(e))


def sync_jobs(
    company_slug: str | None = None,
    stale_hours: float | None = None,
    max_workers: int = 5,
    on_start: "callable | None" = None,
    on_result: "callable | None" = None,
    on_skip: "callable | None" = None,
    on_enrich_start: "callable | None" = None,
    on_enrich_progress: "callable | None" = None,
    on_enrich_done: "callable | None" = None,
    on_embed_start: "callable | None" = None,
    on_embed_progress: "callable | None" = None,
    on_embed_done: "callable | None" = None,
    db_path: "Path | str | None" = None,
) -> list[SyncResult]:
    """Sync job listings from ATS boards into the SQLite cache.

    Args:
        company_slug: Sync only this company (None = all scrapeable companies).
        stale_hours: Skip companies synced within this many hours.
        max_workers: Thread pool size.
        on_start: Callback(slug) when a company fetch begins (called from worker thread).
        on_result: Callback(SyncResult) for each completed company.
        on_skip: Callback(slug, reason) for skipped companies.
        db_path: Override DB path (for testing).

    Returns list of SyncResult for companies that were actually synced.
    """
    from jobbuddy.cache import generate_embeddings, get_connection, is_stale, record_sync_error, upsert_company_jobs

    registry = list_companies()

    # Build list of companies to sync (validate before opening DB)
    if company_slug:
        company = lookup_by_name(company_slug)
        if not company:
            raise ValueError(f"Unknown company: {company_slug}")
        if not company.ats:
            raise ValueError(f"No ATS configured for {company.name}")
        targets = [company]
    else:
        targets = [c for c in registry.values() if c.ats is not None]

    conn = get_connection(db_path, check_same_thread=False)

    # Filter by staleness
    if stale_hours is not None:
        filtered = []
        for c in targets:
            if is_stale(conn, c.slug, stale_hours):
                filtered.append(c)
            elif on_skip:
                on_skip(c.slug, "recently synced")
        targets = filtered

    if not targets:
        conn.close()
        return []

    # Shuffle to spread same-platform companies across time (rate limit mitigation)
    random.shuffle(targets)

    results: list[SyncResult] = []
    slugs_to_embed: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_slug = {}
        start_times: dict[str, float] = {}
        for company in targets:
            start_times[company.slug] = monotonic()
            future = executor.submit(_fetch_company_jobs, company, on_start)
            future_to_slug[future] = company.slug

        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            elapsed = monotonic() - start_times[slug]

            try:
                result_slug, payload = future.result()
            except Exception as e:
                sr = SyncResult(slug, error=str(e), elapsed=elapsed)
                record_sync_error(conn, slug, str(e))
                results.append(sr)
                if on_result:
                    on_result(sr)
                continue

            if isinstance(payload, str):
                # Error string
                sr = SyncResult(slug, error=payload, elapsed=elapsed)
                record_sync_error(conn, slug, payload)
            else:
                # Success — list of Jobs
                upsert_company_jobs(conn, slug, payload)
                slugs_to_embed.append(slug)
                sr = SyncResult(slug, job_count=len(payload), elapsed=elapsed)

            results.append(sr)
            if on_result:
                on_result(sr)

    # Enrich descriptions for stub fetchers (Workday, Eightfold, Oracle HCM)
    # that don't return descriptions in list_jobs(). Runs between fetch and
    # embed phases so newly-enriched descriptions get embedded.
    if slugs_to_embed:
        import json as _json
        from jobbuddy.cache import get_jobs_needing_descriptions, update_job_descriptions

        # Build slug -> company map for fetcher lookup
        slug_to_company = {c.slug: c for c in targets}
        enrich_total = 0
        # (slug, [job_ids], {job_id: metadata_dict})
        enrich_plan: list[tuple[str, list[str], dict[str, dict]]] = []

        for slug in slugs_to_embed:
            company_obj = slug_to_company.get(slug)
            if not company_obj:
                continue
            fetcher = get_fetcher(company_obj)
            if fetcher.descriptions_in_listing:
                continue
            needing = get_jobs_needing_descriptions(conn, slug)
            if not needing:
                continue
            job_ids = [j["job_id"] for j in needing]
            jobs_meta = {
                j["job_id"]: _json.loads(j["ats_metadata"] or "{}")
                for j in needing
            }
            enrich_plan.append((slug, job_ids, jobs_meta))
            enrich_total += len(job_ids)

        if enrich_total > 0:
            if on_enrich_start:
                on_enrich_start(enrich_total)

            import threading
            enrich_done = 0
            enrich_lock = threading.Lock()

            def _enrich_company(slug: str, job_ids: list[str], jobs_meta: dict[str, dict]) -> None:
                nonlocal enrich_done
                company_obj = slug_to_company[slug]
                try:
                    fetcher = get_fetcher(company_obj)

                    def _on_fetched(job_id: str, desc: str, _slug: str = slug) -> None:
                        nonlocal enrich_done
                        with enrich_lock:
                            update_job_descriptions(conn, _slug, {job_id: desc})
                            enrich_done += 1
                            if on_enrich_progress:
                                on_enrich_progress(enrich_done, enrich_total)

                    fetcher.fetch_descriptions(
                        job_ids,
                        metadata=jobs_meta,
                        on_fetched=_on_fetched,
                    )
                except Exception as e:
                    log.warning("Description enrichment failed for %s: %s", slug, e)
                    with enrich_lock:
                        enrich_done += len(job_ids)
                        if on_enrich_progress:
                            on_enrich_progress(enrich_done, enrich_total)

            with ThreadPoolExecutor(max_workers=max_workers) as enrich_executor:
                enrich_futures = [
                    enrich_executor.submit(_enrich_company, slug, job_ids, jobs_meta)
                    for slug, job_ids, jobs_meta in enrich_plan
                ]
                for future in as_completed(enrich_futures):
                    future.result()  # propagate unexpected exceptions

            if on_enrich_done:
                on_enrich_done()

    # Generate embeddings after all fetches complete — doing this inline was
    # blocking the main thread and preventing result callbacks from firing,
    # causing spinners to pile up with nothing completing.
    if slugs_to_embed:
        # Count total jobs needing embeddings across all companies
        from jobbuddy.cache import count_pending_embeddings

        total_jobs = sum(count_pending_embeddings(conn, slug) for slug in slugs_to_embed)
        if on_embed_start:
            on_embed_start(total_jobs)
        if total_jobs > 0:
            jobs_done = 0
            for slug in slugs_to_embed:
                def _progress(done: int, total: int) -> None:
                    nonlocal jobs_done
                    if on_embed_progress:
                        on_embed_progress(jobs_done + done, total_jobs)

                try:
                    count = generate_embeddings(conn, company_slug=slug, on_progress=_progress)
                    jobs_done += count
                except Exception as e:
                    log.warning("Embedding generation failed for %s: %s", slug, e)
        if on_embed_done:
            on_embed_done()

    conn.close()
    return results
