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
from datetime import date
from typing import Any
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


_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}
_ASCTIME_RE = re.compile(
    r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+\d{2}:\d{2}:\d{2}\s+[A-Z]{2,4}\s+(\d{4})$"
)
_LONG_MONTH_RE = re.compile(
    r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})$"
)


def _parse_loose_date(raw: str) -> date | None:
    """Parse a TalentBrew detail-page date in any format we've seen.

    Supported shapes:
      - JSON-LD `datePosted`: "2026-5-1" or "2026-05-01" (Walgreens-style unpadded ok)
      - Microdata `<meta itemprop="datePosted" content="...">`: asctime-like
        "Mon May 04 00:00:00 UTC 2026" (Amtrak)
      - v3 visible "Date posted" label: "May 01, 2026" (Disney)
    """
    if not raw:
        return None
    s = raw.strip()
    m = _DATE_RE.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _ASCTIME_RE.match(s)
    if m:
        month = _MONTHS.get(m.group(1))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
    m = _LONG_MONTH_RE.match(s)
    if m:
        month = _MONTHS.get(m.group(1).title()[:3])
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
    return None


class TalentBrewFetcher(ATSFetcher):
    ats_type = "talentbrew"
    descriptions_in_listing = False
    enrich_delay = 0.0
    enrichment_fills = ("description", "published_at")

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

    def _parse_detail_page(self, html: str) -> dict[str, Any]:
        """Parse a TalentBrew detail page using whichever encoding it uses.

        Returns {"description": str | None, "published_at": date | None}. Three
        variants in the wild — fallback chain runs until one yields data:

          1. JSON-LD JobPosting (Boeing, Walgreens, Citi, Comcast, Spectrum)
          2. schema.org microdata `itemprop=...` attributes (Amtrak)
          3. v3 page shape with `<div class="job-description">` (Disney) — no
             posted date available, falls back to scrape-date downstream.
        """
        result = self._parse_jsonld(html)
        if result["description"] or result["published_at"]:
            return result
        result = self._parse_microdata(html)
        if result["description"] or result["published_at"]:
            return result
        return self._parse_v3_html(html)

    @staticmethod
    def _parse_jsonld(html: str) -> dict[str, Any]:
        ld_pattern = re.compile(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            re.DOTALL,
        )
        for match in ld_pattern.finditer(html):
            try:
                # strict=False: some tenants (Boeing) embed raw tabs/newlines
                # in the description without escaping. Strict JSON rejects
                # those; permissive mode accepts them.
                data = json.loads(match.group(1), strict=False)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "JobPosting":
                continue

            desc_raw = data.get("description")
            description = strip_html(desc_raw) if isinstance(desc_raw, str) and desc_raw else None

            posted_raw = data.get("datePosted")
            published_at = _parse_loose_date(posted_raw) if isinstance(posted_raw, str) else None

            return {"description": description, "published_at": published_at}

        return {"description": None, "published_at": None}

    @staticmethod
    def _parse_microdata(html: str) -> dict[str, Any]:
        """Parse schema.org HTML microdata. Amtrak emits a JobPosting itemscope
        with `<meta itemprop="datePosted" content="Mon May 04 00:00:00 UTC 2026">`
        and `<span itemprop="description">...</span>` containing the body."""
        published_at: date | None = None
        date_match = re.search(
            r'<meta[^>]*itemprop="datePosted"[^>]*content="([^"]+)"',
            html,
        )
        if date_match:
            published_at = _parse_loose_date(date_match.group(1))

        description: str | None = None
        # Match a span/div with itemprop="description" then capture up to the
        # matching closing tag. Microdata can use either tag, so we accept both.
        desc_match = re.search(
            r'<(span|div)[^>]*itemprop="description"[^>]*>(.*?)</\1>',
            html,
            re.DOTALL,
        )
        if desc_match:
            body = strip_html(desc_match.group(2)) if desc_match.group(2) else None
            description = body or None

        return {"description": description, "published_at": published_at}

    @staticmethod
    def _parse_v3_html(html: str) -> dict[str, Any]:
        """Parse the v3 TalentBrew shell (Disney) where the description sits
        in `<div class="ats-description">` and the posted date is rendered as
        a visible `<span class="job-date">…<b>Date posted</b> May 01, 2026</span>`.
        """
        published_at: date | None = None
        date_match = re.search(
            r'<span[^>]*class="[^"]*\bjob-date\b[^"]*"[^>]*>'
            r'.*?<b>\s*Date\s*posted\s*</b>\s*([^<\n]+?)\s*</span>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if date_match:
            published_at = _parse_loose_date(date_match.group(1))

        description: str | None = None
        # ats-description is followed by </section>; capture until the section
        # closes so nested </div> tags inside the body don't terminate early.
        desc_match = re.search(
            r'<div[^>]*class="[^"]*\bats-description\b[^"]*"[^>]*>(.*?)</section>',
            html,
            re.DOTALL,
        )
        if desc_match:
            body = strip_html(desc_match.group(1))
            description = body or None

        return {"description": description, "published_at": published_at}

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description from the job detail page."""
        return self.fetch_enrichment(job_id, metadata).get("description")

    def fetch_enrichment(self, job_id: str, metadata: dict | None = None) -> dict[str, Any]:
        """Fetch description AND posted date from the JSON-LD on a single
        detail-page request. The JSON-LD parse already produces both, so we
        get published_at for free here."""
        self._require_config()

        url = (metadata or {}).get("url")
        if not url:
            jobs = self.list_jobs()
            for j in jobs:
                if j.id == job_id:
                    url = j.url
                    break

        if not url:
            log.warning("No URL found for job %s, cannot fetch enrichment", job_id)
            return {}

        resp = self._retry_request(lambda: self.client.get(url))
        resp.raise_for_status()
        parsed = self._parse_detail_page(resp.text)
        # Strip None values so the write path only updates columns we have data for.
        return {k: v for k, v in parsed.items() if v is not None}
