"""SAP SuccessFactors ATS fetcher.

SuccessFactors career sites are server-rendered HTML. Job listings come from
a search endpoint at /search-jobs/results with page-based pagination (25/page,
server-enforced). Job details are at /job/{slug}/{numericId}/.

Company registry fields:
  - ats: "successfactors"
  - board: company slug (e.g. "paccar")
  - careers_url: full base URL (e.g. "https://jobs.paccar.com")
"""

import html as html_mod
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

_RECORDS_PER_PAGE = 25


def _parse_date(value: str | None) -> date | None:
    """Parse SuccessFactors date format: 'Apr 22, 2026' -> date."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


class SuccessFactorsFetcher(ATSFetcher):
    ats_type = "successfactors"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        careers_url: str = "",
    ):
        super().__init__(board, name)
        self.careers_url = careers_url.rstrip("/")

    def _require_config(self) -> None:
        if not self.careers_url:
            raise ValueError(
                f"SuccessFactors fetcher for '{self.name}' requires careers_url. "
                "This is normally set from the company registry."
            )

    def _search_url(self, page: int) -> str:
        return (
            f"{self.careers_url}/search-jobs/results"
            f"?CurrentPage={page}&RecordsPerPage={_RECORDS_PER_PAGE}"
        )

    def _parse_search_html(self, html: str) -> tuple[list[Job], int]:
        """Parse search results HTML. Returns (jobs, total)."""
        # Total from paginationLabel: Results <b>1 – 25</b> of <b>95</b>
        total = 0
        pagination_match = re.search(
            r'class="paginationLabel"[^>]*>.*?<b>[^<]*</b>\s*of\s*<b>(\d+)</b>',
            html,
            re.DOTALL,
        )
        if pagination_match:
            total = int(pagination_match.group(1))

        jobs: list[Job] = []

        # Split by data-row table rows
        rows = re.findall(
            r'<tr class="data-row">(.*?)</tr>',
            html,
            re.DOTALL,
        )

        for row in rows:
            # Title and URL from jobTitle-link in the hidden-phone span (desktop version)
            link_match = re.search(
                r'<span class="jobTitle hidden-phone">\s*'
                r'<a\s+href="([^"]+)"\s+class="jobTitle-link">([^<]+)</a>',
                row,
                re.DOTALL,
            )
            if not link_match:
                continue

            path = link_match.group(1)
            title = link_match.group(2).strip()

            # Job ID: trailing numeric from the URL path
            # e.g. /job/Renton-Automotive-Painter-WA-98055/1260533301/ -> 1260533301
            id_match = re.search(r'/(\d+)/?$', path)
            if not id_match:
                continue
            job_id = id_match.group(1)

            url = f"{self.careers_url}{path}"

            # Location (from hidden-phone td, not the mobile duplicate)
            location = None
            loc_match = re.search(
                r'<td class="colLocation[^"]*"[^>]*>.*?<span class="jobLocation">([^<]+)</span>',
                row,
                re.DOTALL,
            )
            if loc_match:
                location = loc_match.group(1).strip()

            # Department
            department = None
            dept_match = re.search(
                r'<span class="jobDepartment">([^<]+)</span>',
                row,
            )
            if dept_match:
                department = html_mod.unescape(dept_match.group(1).strip())

            # Date
            published_at = None
            date_match = re.search(
                r'<td class="colDate[^"]*"[^>]*>.*?<span class="jobDate">([^<]+)</span>',
                row,
                re.DOTALL,
            )
            if date_match:
                published_at = _parse_date(date_match.group(1))

            # Facility -> team
            team = None
            fac_match = re.search(
                r'<span class="jobFacility">([^<]+)</span>',
                row,
            )
            if fac_match:
                team = fac_match.group(1).strip()

            jobs.append(
                Job(
                    id=job_id,
                    title=title,
                    location=location or "",
                    url=url,
                    apply_url=url,
                    published_at=published_at,
                    department=department,
                    team=team,
                    ats_metadata={"url": url},
                )
            )

        return jobs, total

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        self._require_config()

        def _fetch_page1():
            resp = self.client.get(self._search_url(1))
            resp.raise_for_status()
            return resp

        resp = self._retry_request(_fetch_page1, on_retry=on_retry)
        jobs, total = self._parse_search_html(resp.text)
        if on_progress:
            on_progress(len(jobs), total)

        if total <= _RECORDS_PER_PAGE:
            return jobs

        total_pages = (total + _RECORDS_PER_PAGE - 1) // _RECORDS_PER_PAGE
        remaining_pages = list(range(2, total_pages + 1))

        lock = threading.Lock()

        def _fetch_page(page: int) -> list[Job]:
            def _do_fetch():
                resp = self.client.get(self._search_url(page))
                resp.raise_for_status()
                return resp
            page_resp = self._retry_request(_do_fetch, on_retry=on_retry)
            page_jobs, _ = self._parse_search_html(page_resp.text)
            return page_jobs

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_page, p): p for p in remaining_pages}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        """Fetch a single job by ID. Finds it in the listing, then enriches with description."""
        self._require_config()
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                desc = self.fetch_description(job_id, metadata={"url": j.url})
                if desc:
                    j = j.model_copy(update={"description": desc})
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.name}.")

    def _extract_description(self, html: str) -> str | None:
        """Extract job description from a detail page's jobdescription div.

        Handles nested <div> tags by counting open/close pairs to find the
        balanced closing tag, rather than stopping at the first </div>.
        """
        marker = '<div class="jobdescription">'
        start = html.find(marker)
        if start == -1:
            return None
        inner_start = start + len(marker)

        depth = 1
        pos = inner_start
        while depth > 0 and pos < len(html):
            next_open = html.find("<div", pos)
            next_close = html.find("</div>", pos)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 4
            else:
                depth -= 1
                if depth == 0:
                    return strip_html(html[inner_start:next_close])
                pos = next_close + 6

        return strip_html(html[inner_start:])

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description from the job detail page."""
        self._require_config()

        url = (metadata or {}).get("url")
        if not url:
            # Build URL from job_id — requires listing to get the slug
            jobs = self.list_jobs()
            for j in jobs:
                if j.id == job_id:
                    url = j.url
                    break

        if not url:
            log.warning("No URL found for job %s, cannot fetch description", job_id)
            return None

        def _fetch_detail():
            resp = self.client.get(url)
            resp.raise_for_status()
            return resp

        resp = self._retry_request(_fetch_detail)
        return self._extract_description(resp.text)
