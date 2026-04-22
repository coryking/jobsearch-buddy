"""SmartRecruiters ATS fetcher.

Public API at api.smartrecruiters.com — no auth required. List endpoint
returns metadata only (no descriptions), so this is a stub fetcher that
needs enrichment to fetch individual job descriptions.

Company registry fields:
  - ats: "smartrecruiters"
  - board: company identifier (e.g. "Wabtec", case-sensitive)
  - country: optional ISO country code filter (e.g. "us")
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

API_BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _build_location(loc: dict) -> str:
    if not loc:
        return ""
    full = loc.get("fullLocation", "")
    if full:
        return full
    parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
    return ", ".join(p for p in parts if p)


def _extract_description(job_ad: dict) -> str | None:
    sections = job_ad.get("sections", {})
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key, {})
        html = section.get("text", "")
        if html:
            text = strip_html(html) if "<" in html else html.strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


class SmartRecruitersFetcher(ATSFetcher):
    ats_type = "smartrecruiters"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        country: str = "",
    ):
        super().__init__(board, name)
        self.country = country

    def _list_url(self) -> str:
        return f"{API_BASE}/{self.board}/postings"

    def _detail_url(self, posting_id: str) -> str:
        return f"{API_BASE}/{self.board}/postings/{posting_id}"

    def _list_params(self, offset: int) -> dict:
        params: dict = {"offset": offset, "limit": PAGE_SIZE}
        if self.country:
            params["country"] = self.country
        return params

    def _posting_url(self, posting_id: str) -> str:
        return f"https://jobs.smartrecruiters.com/{self.board}/{posting_id}"

    def _parse_listing(self, data: dict) -> Job:
        posting_id = str(data.get("id", ""))
        loc = data.get("location", {})
        dept = data.get("department", {})
        return Job(
            id=posting_id,
            title=data.get("name", ""),
            location=_build_location(loc),
            url=self._posting_url(posting_id),
            apply_url=self._posting_url(posting_id),
            published_at=_parse_date(data.get("releasedDate")),
            department=dept.get("label") if dept else None,
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        url = self._list_url()

        def _fetch_page(offset: int):
            resp = self.client.get(url, params=self._list_params(offset))
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(lambda: _fetch_page(0), on_retry=on_retry)
        total = data.get("totalFound", 0)
        raw_jobs = data.get("content", [])
        jobs: JobList = [self._parse_listing(j) for j in raw_jobs]

        if on_progress:
            on_progress(len(jobs), total)

        remaining_offsets = list(range(PAGE_SIZE, total, PAGE_SIZE))
        if not remaining_offsets:
            return jobs

        lock = threading.Lock()

        def _fetch_remaining(offset: int) -> list[Job]:
            def _do_fetch():
                page_data = self.client.get(url, params=self._list_params(offset))
                page_data.raise_for_status()
                return [self._parse_listing(j) for j in page_data.json().get("content", [])]
            return self._retry_request(_do_fetch, on_retry=on_retry)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_remaining, off): off for off in remaining_offsets}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, total)

        return jobs

    def _fetch_detail(self, posting_id: str) -> dict:
        resp = self.client.get(self._detail_url(posting_id))
        resp.raise_for_status()
        return resp.json()

    def fetch_job(self, job_id: str) -> Job:
        data = self._fetch_detail(job_id)
        if not data or not data.get("id"):
            raise ValueError(f"Posting {job_id} not found on {self.board}.")

        loc = data.get("location", {})
        dept = data.get("department", {})
        job_ad = data.get("jobAd", {})
        pid = str(data["id"])

        posting_url = data.get("postingUrl", self._posting_url(pid))
        apply_url = data.get("applyUrl", posting_url)

        return Job(
            id=pid,
            title=data.get("name", ""),
            location=_build_location(loc),
            url=posting_url,
            apply_url=apply_url,
            published_at=_parse_date(data.get("releasedDate")),
            department=dept.get("label") if dept else None,
            description=_extract_description(job_ad),
        )

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:  # noqa: ARG002
        data = self._fetch_detail(job_id)
        return _extract_description(data.get("jobAd", {}))
