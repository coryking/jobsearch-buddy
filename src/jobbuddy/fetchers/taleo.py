"""Oracle Taleo Enterprise ATS fetcher.

Taleo career sites expose a JSON REST endpoint at
`/careersection/rest/jobboard/searchjobs`. Each tenant requires a portal ID
that is embedded in the `jobsearch.ftl` page source — this fetcher discovers
it automatically on the first `list_jobs()` call. Set `taleo_portal` in the
company registry to skip auto-discovery.

Job descriptions are not included in the listing response; the enrich phase
fetches them individually from the detail page.

Company registry fields:
  - ats: "taleo"
  - board: subdomain prefix (e.g. "textron" for textron.taleo.net)
  - taleo_section: career section path component (e.g. "textron", "2")
  - taleo_portal: portal ID (optional; auto-discovered from jobsearch.ftl if absent)
"""

import copy
import logging
import re
import threading
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 25
MAX_PAGES = 200

_LIST_BODY_TEMPLATE: dict = {
    "multilineEnabled": False,
    "sortingSelection": {
        "sortBySelectionParam": "1",
        "ascendingSortingOrder": "false",
    },
    "fieldData": {
        "fields": {"KEYWORD": "", "LOCATION": ""},
        "valid": True,
    },
    "filterSelectionParam": {
        "searchFilterSelections": [
            {"id": "LOCATION", "selectedValues": []},
            {"id": "JOB_FIELD", "selectedValues": []},
            {"id": "ORGANIZATION", "selectedValues": []},
            {"id": "JOB_SCHEDULE", "selectedValues": []},
            {"id": "JOB_TYPE", "selectedValues": []},
            {"id": "JOB_LEVEL", "selectedValues": []},
            {"id": "WILL_TRAVEL", "selectedValues": []},
            {"id": "POSTING_DATE", "selectedValues": []},
        ]
    },
    "advancedSearchFiltersSelectionParam": {
        "searchFilterSelections": [
            {"id": "LOCATION", "selectedValues": []},
            {"id": "JOB_FIELD", "selectedValues": []},
            {"id": "ORGANIZATION", "selectedValues": []},
            {"id": "JOB_NUMBER", "selectedValues": []},
            {"id": "JOB_LEVEL", "selectedValues": []},
            {"id": "JOB_SHIFT", "selectedValues": []},
            {"id": "JOB_SCHEDULE", "selectedValues": []},
            {"id": "JOB_TYPE", "selectedValues": []},
            {"id": "WILL_TRAVEL", "selectedValues": []},
            {"id": "URGENT_JOB", "selectedValues": []},
            {"id": "STUDY_LEVEL", "selectedValues": []},
        ]
    },
    "pageNo": 1,
}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    from dateutil import parser as date_parser
    try:
        return date_parser.parse(value.strip()).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _depth_extract(html: str, start_marker: str) -> str | None:
    """Extract the content of an HTML element found by start_marker using depth counting.

    Finds the first occurrence of start_marker (which must begin with '<div'),
    then tracks nested <div>...</div> pairs to find the matching closing tag.
    Returns the stripped text content, or None if the marker is not found.
    """
    start = html.find(start_marker)
    if start == -1:
        return None
    inner_start = start + len(start_marker)
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
                return strip_html(html[inner_start:next_close]) or None
            pos = next_close + 6
    return strip_html(html[inner_start:]) or None


class TaleoFetcher(ATSFetcher):
    ats_type = "taleo"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        taleo_section: str = "",
        taleo_portal: str = "",
    ):
        super().__init__(board, name)
        self.taleo_section = taleo_section or board
        self.taleo_portal = taleo_portal
        self._portal_id: str | None = taleo_portal or None
        self._portal_lock = threading.Lock()

    def _base_url(self) -> str:
        return f"https://{self.board}.taleo.net"

    def _search_url(self, portal_id: str) -> str:
        return f"{self._base_url()}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal_id}"

    def _detail_url(self, job_id: str) -> str:
        return (
            f"{self._base_url()}/careersection/{self.taleo_section}"
            f"/jobdetail.ftl?job={job_id}&lang=en"
        )

    def _ftl_url(self) -> str:
        return f"{self._base_url()}/careersection/{self.taleo_section}/jobsearch.ftl?lang=en"

    def _discover_portal_id(self) -> str:
        """Extract portal ID from the jobsearch.ftl page source."""
        resp = self.client.get(self._ftl_url())
        resp.raise_for_status()
        html = resp.text

        # Pattern 1: embedded in a REST API URL reference (most reliable)
        m = re.search(r"rest/jobboard/searchjobs[^\"']*portal=([A-Za-z0-9_-]+)", html)
        if m:
            return m.group(1)

        # Pattern 2: JSON/JS variable with key "portal" or "portalId"
        m = re.search(r"""['"](portal|portalId)['"]\s*[=:]\s*['"](\d+)['"]""", html)
        if m:
            return m.group(2)

        raise ValueError(
            f"Cannot discover Taleo portal ID for '{self.name}' "
            f"from {self._ftl_url()}. "
            "Set taleo_portal in the company registry to override auto-discovery."
        )

    def _get_portal_id(self, *, on_retry: RetryCallback | None = None) -> str:
        """Return cached portal ID, discovering it on first call (lock-free during I/O)."""
        with self._portal_lock:
            if self._portal_id is not None:
                return self._portal_id

        # Discover outside the lock to avoid holding it during HTTP I/O.
        portal_id = self._retry_request(self._discover_portal_id, on_retry=on_retry)

        with self._portal_lock:
            if self._portal_id is None:
                self._portal_id = portal_id
            return self._portal_id

    def _list_body(self, page: int) -> dict:
        body = copy.deepcopy(_LIST_BODY_TEMPLATE)
        body["pageNo"] = page
        return body

    def _parse_requisition(self, item: dict) -> Job | None:
        """Parse one requisition dict from the REST response."""
        job_id = str(item.get("contestNo") or item.get("jobId") or "")
        if not job_id:
            return None
        title = item.get("requisitionTitle") or item.get("jobTitle") or ""
        location = item.get("primaryLocation") or item.get("location") or ""
        department = item.get("jobField") or item.get("department") or None

        dates = item.get("dates", {}) if isinstance(item.get("dates"), dict) else {}
        raw_date = dates.get("openingDate") or item.get("postingDate")
        published_at = _parse_date(raw_date)

        url = self._detail_url(job_id)
        return Job(
            id=job_id,
            title=title,
            location=location,
            url=url,
            apply_url=url,
            published_at=published_at,
            department=department or None,
        )

    def _fetch_page(
        self,
        page: int,
        portal_id: str,
        *,
        on_retry: RetryCallback | None = None,
    ) -> tuple[list[Job], int]:
        url = self._search_url(portal_id)
        body = self._list_body(page)

        def _do():
            resp = self.client.post(url, json=body)
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(_do, on_retry=on_retry)
        reqs = data.get("requisitionList", [])
        total = data.get("requisitionCount", len(reqs))

        jobs = [j for j in (self._parse_requisition(r) for r in reqs) if j is not None]
        return jobs, total

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        portal_id = self._get_portal_id(on_retry=on_retry)

        jobs, total = self._fetch_page(1, portal_id, on_retry=on_retry)
        if on_progress:
            on_progress(len(jobs), total)

        page = 2
        while len(jobs) < total and page <= MAX_PAGES:
            page_jobs, _ = self._fetch_page(page, portal_id, on_retry=on_retry)
            if not page_jobs:
                break
            jobs.extend(page_jobs)
            page += 1
            if on_progress:
                on_progress(len(jobs), total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        portal_id = self._get_portal_id()
        page = 1
        while page <= MAX_PAGES:
            page_jobs, total = self._fetch_page(page, portal_id)
            for j in page_jobs:
                if j.id == job_id:
                    desc = self.fetch_description(job_id, metadata={"url": j.url})
                    if desc:
                        return j.model_copy(update={"description": desc})
                    return j
            if len(page_jobs) == 0 or page * PAGE_SIZE >= total:
                break
            page += 1
        raise ValueError(f"Job ID {job_id} not found on {self.name} ({self.board}.taleo.net).")

    def _extract_description(self, html: str) -> str | None:
        """Extract job description from a Taleo detail page using depth-counting."""
        # Try known div markers in preference order
        for search_pat in [
            r'<div[^>]+class="[^"]*\bjob-description\b[^"]*"',
            r'<div[^>]+class="[^"]*\bjobDescription\b[^"]*"',
            r'<div[^>]+id="[^"]*\bjob[-_]?description\b[^"]*"',
            r'<div[^>]+id="requisitionDescriptionInterface"',
        ]:
            m = re.search(search_pat, html, re.IGNORECASE)
            if m:
                marker = m.group(0) + ">"
                text = _depth_extract(html, marker)
                if text:
                    return text
        return None

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        url = (metadata or {}).get("url") or self._detail_url(job_id)

        def _fetch():
            resp = self.client.get(url)
            resp.raise_for_status()
            return resp

        resp = self._retry_request(_fetch)
        return self._extract_description(resp.text)
