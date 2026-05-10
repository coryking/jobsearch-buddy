"""Jibe (iCIMS Attract/CRM) ATS fetcher.

Jibe is the candidate-facing layer for iCIMS career sites. It exposes a clean
JSON API at {careers_url}/api/jobs with page-based pagination (max 100/page).
Descriptions are included in the listing response as HTML.

Company registry fields:
  - ats: "jibe"
  - board: client code (e.g. "spiritaero")
  - careers_url: full base URL (e.g. "https://careers.spiritaero.com")
"""

import logging
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 100


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _build_location(data: dict) -> str:
    parts = [data.get("city", ""), data.get("state", ""), data.get("country", "")]
    return ", ".join(p for p in parts if p)


class JibeFetcher(ATSFetcher):
    ats_type = "jibe"

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
                f"Jibe fetcher for '{self.name}' requires careers_url. "
                "This is normally set from the company registry."
            )

    def _api_url(self, page: int) -> str:
        return f"{self.careers_url}/api/jobs?page={page}&limit={PAGE_SIZE}"

    def _job_url(self, slug: str) -> str:
        return f"{self.careers_url}/jobs/{slug}"

    def _parse_job(self, data: dict) -> Job:
        slug = data.get("slug", "")
        desc_html = data.get("description", "")
        if desc_html:
            description = strip_html(desc_html) if "<" in desc_html else desc_html.strip()
        else:
            parts = []
            for field in ("qualifications", "responsibilities"):
                html = data.get(field, "")
                if html:
                    text = strip_html(html) if "<" in html else html
                    if text.strip():
                        parts.append(text.strip())
            description = "\n\n".join(parts) if parts else None

        salary = None
        sal_min = data.get("salary_min_value", 0)
        sal_max = data.get("salary_max_value", 0)
        if sal_min and sal_max:
            salary = f"{sal_min} - {sal_max}"
        elif sal_min:
            salary = str(sal_min)
        elif sal_max:
            salary = str(sal_max)

        categories = data.get("categories", [])
        department = categories[0]["name"] if categories else None

        # Jibe payloads carry both `posted_date` (first-listed) and
        # `update_date` (last touch in iCIMS). Fall back to the iCIMS
        # nested `meta_data.icims.date_updated` when `update_date` is
        # absent on older payloads.
        update_date = data.get("update_date")
        if not update_date:
            meta = data.get("meta_data") or {}
            icims = meta.get("icims") or {}
            update_date = icims.get("date_updated")

        return Job(
            id=slug,
            title=data.get("title", ""),
            location=_build_location(data),
            url=self._job_url(slug),
            apply_url=data.get("apply_url", self._job_url(slug)),
            published_at=_parse_date(data.get("posted_date")),
            last_listing_update=_parse_date(update_date),
            department=department,
            salary=salary,
            description=description,
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        self._require_config()

        def _fetch_page(page: int):
            resp = self.client.get(self._api_url(page))
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(lambda: _fetch_page(1), on_retry=on_retry)
        total = data.get("totalCount", 0)
        raw_jobs = data.get("jobs", [])
        jobs: JobList = [self._parse_job(j["data"]) for j in raw_jobs]

        if on_progress:
            on_progress(len(jobs), total)

        if len(jobs) >= total:
            return jobs

        page = 2
        while len(jobs) < total:
            page_data = self._retry_request(lambda p=page: _fetch_page(p), on_retry=on_retry)
            page_jobs = page_data.get("jobs", [])
            if not page_jobs:
                break
            jobs.extend(self._parse_job(j["data"]) for j in page_jobs)
            if on_progress:
                on_progress(len(jobs), total)
            page += 1

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.name}.")
