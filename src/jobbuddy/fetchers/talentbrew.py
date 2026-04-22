"""TalentBrew (Radancy) ATS fetcher.

TalentBrew career sites expose a search API at /search-jobs/results that returns
JSON with HTML fragments. Job detail pages include JSON-LD structured data.

Company registry fields:
  - ats: "talentbrew"
  - board: not used (set to empty string)
  - tb_host: e.g. "jobs.intuit.com"
  - tb_tenant_id: numeric tenant ID from the URL path (e.g. 27595)
"""

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

# Query params that stay constant across pages
_SEARCH_DEFAULTS = {
    "ActiveFacetID": "0",
    "Distance": "50",
    "RadiusUnitType": "0",
    "Keywords": "",
    "Location": "",
    "ShowRadius": "False",
    "IsPagination": "False",
    "CustomFacetName": "",
    "FacetTerm": "",
    "FacetType": "0",
    "SearchResultsModuleName": "Search Results",
    "SearchFiltersModuleName": "Search Filters",
    "SortCriteria": "0",
    "SortDirection": "0",
    "SearchType": "5",
    "PostalCode": "",
    "ResultsType": "0",
    "fc": "",
    "fl": "",
    "fcf": "",
    "afc": "",
    "afl": "",
    "afcf": "",
}

_RECORDS_PER_PAGE = 20


class TalentBrewFetcher(ATSFetcher):
    ats_type = "talentbrew"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        tb_host: str = "",
        tb_tenant_id: int = 0,
    ):
        super().__init__(board, name)
        self.tb_host = tb_host
        self.tb_tenant_id = tb_tenant_id

    def _require_config(self) -> None:
        if not self.tb_host or not self.tb_tenant_id:
            raise ValueError(
                f"TalentBrew fetcher for '{self.name}' requires tb_host and tb_tenant_id. "
                "These are normally set from the company registry."
            )

    def _base_url(self) -> str:
        return f"https://{self.tb_host}"

    def _search_url(self, page: int) -> str:
        params = {
            **_SEARCH_DEFAULTS,
            "CurrentPage": str(page),
            "RecordsPerPage": str(_RECORDS_PER_PAGE),
        }
        return f"{self._base_url()}/search-jobs/results?{urlencode(params)}"

    def _parse_results_html(self, html: str) -> tuple[list[Job], int]:
        """Parse the results HTML fragment. Returns (jobs, total_results).

        Handles multiple TalentBrew template variants:
        - Intuit style: <a href="/job/..." data-job-id="..." data-title="...">
        - Walgreens style: <a href="/en/job/..." data-job-id="..."><h2>Title</h2>
        - Disney style: <tr>/<td> table layout instead of <li> cards
        """
        total_match = re.search(r'data-total-results="(\d+)"', html)
        total = int(total_match.group(1)) if total_match else 0

        jobs: list[Job] = []

        # Link pattern: <a ... href="...job/..." ... data-job-id="...">
        # href and data-job-id can appear in any order; other attrs may come first.
        link_pattern = re.compile(
            r'<a\s[^>]*href="((?:/[a-z]{2})?/job/[^"]+)"[^>]*data-job-id="(\d+)"[^>]*>',
            re.DOTALL,
        )
        loc_pattern = re.compile(
            r'<span\s+[^>]*class="[^"]*(?:job.location|location)[^"]*"[^>]*>([^<]*)</span>'
        )

        items = re.split(r"<(?:li|tr)[\s>]", html)
        for item in items[1:]:  # skip content before first <li
            link_match = link_pattern.search(item)
            if not link_match:
                continue

            path = link_match.group(1)
            tb_job_id = link_match.group(2)

            # Title: prefer data-title attr, then <h2>, then <span class="..job-title">
            title_attr = re.search(r'data-title="([^"]+)"', item)
            if title_attr:
                title = title_attr.group(1)
            else:
                title_el = re.search(
                    r'<(?:h2|span)[^>]*class="[^"]*(?:headline|job.title)[^"]*"[^>]*>\s*(.*?)\s*</(?:h2|span)>',
                    item, re.DOTALL,
                )
                if not title_el:
                    title_el = re.search(r'<h[23][^>]*>\s*(.*?)\s*</h[23]>', item, re.DOTALL)
                raw = title_el.group(1) if title_el else ""
                title = re.sub(r'<[^>]+>', '', raw)  # strip nested tags
                title = re.sub(r'\s+', ' ', title).strip()

            if not title:
                continue

            # Category from data-category (not all templates have this)
            cat_match = re.search(r"""data-category=['"]([^'"]+)['"]""", item)
            category = cat_match.group(1) if cat_match else None

            loc_match = loc_pattern.search(item)
            location = loc_match.group(1).strip() if loc_match else ""

            url = f"{self._base_url()}{path}"

            jobs.append(
                Job(
                    id=tb_job_id,
                    title=title,
                    location=location,
                    url=url,
                    apply_url=url,
                    department=category,
                    team=category,
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

        # Fetch page 1 to learn total
        resp = self._retry_request(
            lambda: self.client.get(
                self._search_url(1),
                headers={"X-Requested-With": "XMLHttpRequest"},
            ),
            on_retry=on_retry,
        )
        resp.raise_for_status()
        data = resp.json()

        jobs, total = self._parse_results_html(data.get("results", ""))
        if on_progress:
            on_progress(len(jobs), total)

        if total <= _RECORDS_PER_PAGE:
            return jobs

        total_pages = (total + _RECORDS_PER_PAGE - 1) // _RECORDS_PER_PAGE
        remaining_pages = list(range(2, total_pages + 1))

        lock = threading.Lock()

        def _fetch_page(page: int) -> list[Job]:
            page_resp = self._retry_request(
                lambda p=page: self.client.get(
                    self._search_url(p),
                    headers={"X-Requested-With": "XMLHttpRequest"},
                ),
                on_retry=on_retry,
            )
            page_resp.raise_for_status()
            page_data = page_resp.json()
            page_jobs, _ = self._parse_results_html(page_data.get("results", ""))
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
                desc = self.fetch_description(job_id, metadata=None)
                if desc:
                    j = j.model_copy(update={"description": desc})
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.name}.")

    def _extract_description_from_html(self, html: str) -> str | None:
        """Extract job description from a detail page's JSON-LD or HTML."""
        # Try JSON-LD first
        ld_pattern = re.compile(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            re.DOTALL,
        )
        for match in ld_pattern.finditer(html):
            try:
                data = json.loads(match.group(1))
                if data.get("@type") == "JobPosting" and data.get("description"):
                    return strip_html(data["description"])
            except (json.JSONDecodeError, TypeError):
                continue

        return None

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description from the job detail page."""
        self._require_config()

        # We need the URL. If we have it from a prior listing, great.
        # Otherwise we have to search for it.
        url = (metadata or {}).get("url")
        if not url:
            # Try to find the job in the listing
            jobs = self.list_jobs()
            for j in jobs:
                if j.id == job_id:
                    url = j.url
                    break

        if not url:
            log.warning("No URL found for job %s, cannot fetch description", job_id)
            return None

        resp = self._retry_request(lambda: self.client.get(url))
        resp.raise_for_status()
        return self._extract_description_from_html(resp.text)
