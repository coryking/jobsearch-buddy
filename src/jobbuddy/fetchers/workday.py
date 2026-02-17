"""Workday ATS fetcher."""

import logging
import time

import httpx

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)


def _wd_base(wd_company: str, wd_instance: int) -> str:
    return f"https://{wd_company}.wd{wd_instance}.myworkdayjobs.com"


# Browser-like headers to avoid rate limiting / blocks
_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
}


class WorkdayFetcher(ATSFetcher):
    ats_type = "workday"
    descriptions_in_listing = False

    def __init__(self, board: str, name: str | None = None, *, wd_company: str = "", wd_instance: int = 0, default_filters: dict | None = None):
        super().__init__(board, name)
        self.wd_company: str = wd_company
        self.wd_instance: int = wd_instance
        self.default_filters: dict = default_filters or {}
        self._headers = {**_HEADERS, "Referer": f"{_wd_base(wd_company, wd_instance)}/{board}"}

    def _require_config(self) -> None:
        if not self.wd_company or not self.wd_instance:
            raise ValueError(
                f"Workday fetcher for '{self.board}' requires wd_company and wd_instance. "
                "These are normally set from the company registry."
            )

    def list_jobs(self) -> list[Job]:
        self._require_config()
        base = _wd_base(self.wd_company, self.wd_instance)
        api_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}/jobs"

        jobs = []
        offset = 0
        limit = 20
        total = None
        while True:
            body: dict = {"limit": limit, "offset": offset, **self.default_filters}
            resp = httpx.post(api_url, json=body, headers=self._headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break
            # Workday only returns total on the first page
            if total is None:
                total = data.get("total", 0)
            for p in postings:
                ext_path = p.get("externalPath", "")
                job_id = p.get("bulletFields", [""])[0] if p.get("bulletFields") else ext_path
                jobs.append(
                    Job(
                        id=job_id,
                        title=p.get("title", ""),
                        location=p.get("locationsText", ""),
                        url=f"{base}/{self.board}{ext_path}",
                        apply_url=f"{base}/{self.board}{ext_path}",
                        published_at=p.get("postedOn"),
                        department=None,
                        team=None,
                        ext_path=ext_path,
                    )
                )
            offset += limit
            if offset >= total:
                break
            time.sleep(0.5)
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

        ext_path = target.ext_path
        if not ext_path:
            return target

        base = _wd_base(self.wd_company, self.wd_instance)
        detail_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}{ext_path}"
        resp = httpx.get(detail_url, headers=self._headers, timeout=30)
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

    def fetch_descriptions(self, job_ids: list[str]) -> dict[str, str | None]:
        """Fetch descriptions by listing all jobs once to get ext_paths, then hitting detail endpoints.

        Avoids calling list_jobs() per job ID (which is what fetch_job() does).
        """
        self._require_config()
        results: dict[str, str | None] = {jid: None for jid in job_ids}
        needed = set(job_ids)

        # One pass through list_jobs to build job_id -> ext_path map
        try:
            all_jobs = self.list_jobs()
        except Exception as e:
            log.warning("Workday list_jobs failed during enrichment: %s", e)
            return results

        id_to_ext: dict[str, str] = {}
        for j in all_jobs:
            if j.id in needed and j.ext_path:
                id_to_ext[j.id] = j.ext_path

        # Fetch detail endpoints for each matched job
        base = _wd_base(self.wd_company, self.wd_instance)
        for job_id, ext_path in id_to_ext.items():
            try:
                detail_url = f"{base}/wday/cxs/{self.wd_company}/{self.board}{ext_path}"
                resp = httpx.get(detail_url, headers=self._headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                desc = data.get("jobPostingInfo", {}).get("jobDescription", "")
                if desc:
                    results[job_id] = strip_html(desc)
                time.sleep(0.3)
            except Exception as e:
                log.warning("Failed to fetch Workday description for %s: %s", job_id, e)

        return results
