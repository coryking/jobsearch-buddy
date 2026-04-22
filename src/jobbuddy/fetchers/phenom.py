"""Phenom People ATS fetcher.

Used by BAE Systems, RTX/Raytheon, GE Aerospace, Collins Aerospace, etc.
All requests go to POST https://{careers_domain}/widgets with a JSON body
specifying the DDO key (refineSearch for listings, jobDetail for details).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 20


def _parse_posted_date(value: str | None) -> date | None:
    """Parse Phenom ISO timestamp (e.g. '2026-02-02T16:02:16.519+0000') to date."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class PhenomFetcher(ATSFetcher):
    ats_type = "phenom"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        careers_url: str = "",
        locale: str = "en_global",
        country: str = "global",
        page_id: str = "page1-migration",
    ):
        super().__init__(board, name)
        self.careers_url = careers_url.rstrip("/")
        self.locale = locale
        self.country = country
        self.page_id = page_id
        if self.careers_url:
            self.client.headers["Origin"] = self.careers_url
            self.client.headers["Referer"] = f"{self.careers_url}/{self.country}/{self._locale_short()}/search-results"

    def _locale_short(self) -> str:
        """Extract the language code from locale (e.g. 'en_global' -> 'en')."""
        return self.locale.split("_")[0] if "_" in self.locale else self.locale

    def _require_config(self) -> None:
        if not self.careers_url:
            raise ValueError(
                f"Phenom fetcher for '{self.board}' requires careers_url. "
                "This is normally set from the company registry."
            )

    def _widgets_url(self) -> str:
        return f"{self.careers_url}/widgets"

    def _job_url(self, job_id: str) -> str:
        return f"{self.careers_url}/{self.country}/{self._locale_short()}/job/{job_id}"

    def _refine_search_body(self, offset: int) -> dict:
        return {
            "lang": self.locale,
            "deviceType": "desktop",
            "country": self.country,
            "pageName": "search-results",
            "ddoKey": "refineSearch",
            "sortBy": "",
            "substring": "",
            "from": offset,
            "jobs": True,
            "counts": True,
            "all_fields": ["category", "country", "state", "city", "type", "subCategory"],
            "size": PAGE_SIZE,
            "clearAll": False,
            "jdsource": "facets",
            "isSlider": False,
            "pageId": self.page_id,
            "siteType": "external",
            "keywords": "",
        }

    def _job_detail_body(self, job_id: str) -> dict:
        return {
            "lang": self.locale,
            "deviceType": "desktop",
            "country": self.country,
            "pageName": "job",
            "ddoKey": "jobDetail",
            "jobId": job_id,
            "siteType": "external",
            "pageId": self.page_id,
        }

    def _parse_listing(self, raw: dict) -> Job:
        """Map a job object from refineSearch response to a Job."""
        job_id = raw.get("jobId", "")
        return Job(
            id=job_id,
            title=raw.get("title", ""),
            location=raw.get("location", ""),
            url=self._job_url(job_id),
            apply_url=raw.get("applyUrl", self._job_url(job_id)),
            published_at=_parse_posted_date(raw.get("postedDate")),
            department=raw.get("category"),
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        self._require_config()
        url = self._widgets_url()

        # Fetch page 0 to learn total
        resp = self.client.post(url, json=self._refine_search_body(0))
        resp.raise_for_status()
        data = resp.json()

        refine = data.get("refineSearch", {})
        total = refine.get("totalHits", 0)
        raw_jobs = refine.get("data", {}).get("jobs", [])
        jobs: JobList = [self._parse_listing(j) for j in raw_jobs]

        if on_progress:
            on_progress(len(jobs), total)

        # Use actual page size from first response for offset calculation —
        # the API may return fewer items than the requested `size`.
        page_size = len(raw_jobs) or PAGE_SIZE
        remaining_offsets = list(range(page_size, total, page_size))
        if not remaining_offsets:
            return jobs

        lock = threading.Lock()

        def _fetch_page(offset: int) -> list[Job]:
            page_resp = self.client.post(url, json=self._refine_search_body(offset))
            page_resp.raise_for_status()
            page_data = page_resp.json()
            page_refine = page_data.get("refineSearch", {})
            page_jobs = page_refine.get("data", {}).get("jobs", [])
            return [self._parse_listing(j) for j in page_jobs]

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_page, off): off for off in remaining_offsets}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, total)

        return jobs

    def _fetch_detail(self, job_id: str) -> dict:
        """Fetch job detail from the jobDetail DDO."""
        resp = self.client.post(self._widgets_url(), json=self._job_detail_body(job_id))
        resp.raise_for_status()
        data = resp.json()
        return data.get("jobDetail", {}).get("data", {}).get("job", {})

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        job_data = self._fetch_detail(job_id)

        if not job_data or not job_data.get("jobId"):
            raise ValueError(f"Job {job_id} not found on {self.board} board.")

        description = job_data.get("description", "")
        if description:
            description = strip_html(description)

        return Job(
            id=job_data["jobId"],
            title=job_data.get("title", ""),
            location=job_data.get("location", ""),
            url=self._job_url(job_data["jobId"]),
            apply_url=job_data.get("applyUrl", self._job_url(job_data["jobId"])),
            published_at=_parse_posted_date(job_data.get("postedDate")),
            department=job_data.get("category"),
            salary=job_data.get("payRange"),
            description=description or None,
        )

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description directly from detail API (for enrichment)."""
        self._require_config()
        job_data = self._fetch_detail(job_id)
        desc = job_data.get("description", "")
        return strip_html(desc) if desc else None
