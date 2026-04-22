"""Workday ATS fetcher."""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)


def _parse_posted_on(value: str | None) -> date | None:
    """Parse Workday postedOn field, which may be ISO date or relative like 'Posted Today'."""
    if not value:
        return None
    # Try ISO date first (some instances return "2026-03-20")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    # Relative dates: "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    v = value.lower().strip()
    if "today" in v:
        return date.today()
    if "yesterday" in v:
        return date.today() - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?\s*ago", v)
    if m:
        return date.today() - timedelta(days=int(m.group(1)))
    return None


def _wd_base(wd_company: str, wd_instance: int) -> str:
    return f"https://{wd_company}.wd{wd_instance}.myworkdayjobs.com"


class WorkdayFetcher(ATSFetcher):
    ats_type = "workday"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(self, board: str, name: str | None = None, *, wd_company: str = "", wd_instance: int = 0, default_filters: dict | None = None):
        super().__init__(board, name)
        self.wd_company: str = wd_company
        self.wd_instance: int = wd_instance
        self.default_filters: dict = default_filters or {}
        self.client.headers["Referer"] = f"{_wd_base(wd_company, wd_instance)}/{board}"

    def _require_config(self) -> None:
        if not self.wd_company or not self.wd_instance:
            raise ValueError(
                f"Workday fetcher for '{self.board}' requires wd_company and wd_instance. "
                "These are normally set from the company registry."
            )

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> JobList:
        self._require_config()
        base = _wd_base(self.wd_company, self.wd_instance)
        api_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}/jobs"
        limit = 20

        def _parse_posting(p: dict) -> Job:
            ext_path = p.get("externalPath", "")
            job_id = p.get("bulletFields", [""])[-1] if p.get("bulletFields") else ext_path
            return Job(
                id=job_id,
                title=p.get("title", ""),
                location=p.get("locationsText", ""),
                url=f"{base}/{self.board}{ext_path}",
                apply_url=f"{base}/{self.board}{ext_path}",
                published_at=_parse_posted_on(p.get("postedOn")),
                department=None,
                team=None,
                ats_metadata={"ext_path": ext_path} if ext_path else None,
            )

        # Fetch page 0 to learn total
        body: dict = {"limit": limit, "offset": 0, **self.default_filters}
        resp = self.client.post(api_url, json=body)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total", 0)
        jobs: JobList = [_parse_posting(p) for p in data.get("jobPostings", [])]
        if on_progress:
            on_progress(len(jobs), total)

        remaining_offsets = list(range(limit, total, limit))
        if not remaining_offsets:
            return jobs

        lock = threading.Lock()

        def _fetch_page(offset: int) -> list[Job]:
            page_body: dict = {"limit": limit, "offset": offset, **self.default_filters}
            page_resp = self.client.post(api_url, json=page_body)
            page_resp.raise_for_status()
            return [_parse_posting(p) for p in page_resp.json().get("jobPostings", [])]

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

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        jobs = self.list_jobs()
        target = None
        for j in jobs:
            if j.id == job_id:
                target = j
                break
        if not target:
            raise ValueError(f"Job ID {job_id} not found on {self.board} board.")

        ext_path = (target.ats_metadata or {}).get("ext_path")
        if not ext_path:
            return target

        base = _wd_base(self.wd_company, self.wd_instance)
        detail_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}{ext_path}"
        resp = self.client.get(detail_url)
        resp.raise_for_status()
        data = resp.json()

        updates = {}
        desc = data.get("jobPostingInfo", {}).get("jobDescription", "")
        if desc:
            updates["description"] = strip_html(desc)

        pay = data.get("jobPostingInfo", {}).get("payRange")
        if pay:
            updates["salary"] = pay

        if updates:
            target = target.model_copy(update=updates)

        return target

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description using ext_path from metadata (avoids re-listing)."""
        self._require_config()
        ext_path = (metadata or {}).get("ext_path")
        if not ext_path:
            return None

        base = _wd_base(self.wd_company, self.wd_instance)
        detail_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}{ext_path}"
        resp = self.client.get(detail_url)
        resp.raise_for_status()
        data = resp.json()
        desc = data.get("jobPostingInfo", {}).get("jobDescription", "")
        return strip_html(desc) if desc else None
