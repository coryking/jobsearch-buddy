"""Uber careers fetcher (custom API).

Uber uses a custom careers REST API. All listings are returned via a POST
to /api/loadSearchJobsResults, full descriptions included — no enrichment
step required.

Company registry fields:
  - ats: "uber"
  - board: any value (ignored — single global board)
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job

log = logging.getLogger(__name__)

LIST_URL = "https://www.uber.com/api/loadSearchJobsResults?localeCode=en"
PAGE_SIZE = 1000


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _build_location(item: dict) -> str:
    loc = item.get("location")
    if isinstance(loc, dict):
        parts = [loc.get("city", ""), loc.get("state", ""), loc.get("country", "")]
        joined = ", ".join(p for p in parts if p)
        if joined:
            return joined
    elif isinstance(loc, str) and loc:
        return loc

    all_locs = item.get("allLocations") or []
    if all_locs:
        return "; ".join(
            (loc if isinstance(loc, str) else str(loc))
            for loc in all_locs
            if loc
        )
    return ""


class UberFetcher(ATSFetcher):
    ats_type = "uber"
    descriptions_in_listing = True
    enrich_delay = 0.0

    def __init__(self, board: str, name: str | None = None):
        super().__init__(board, name or "Uber")
        self.client.headers.update({
            "content-type": "application/json",
            "x-csrf-token": "x",
            "origin": "https://www.uber.com",
        })

    def _fetch_page(
        self,
        offset: int = 0,
        *,
        on_retry: RetryCallback | None = None,
    ) -> dict:
        payload: dict = {"params": {"location": [], "department": [], "team": []}}
        if offset:
            payload["offset"] = offset

        def _do():
            resp = self.client.post(LIST_URL, json=payload)
            resp.raise_for_status()
            return resp.json()

        return self._retry_request(_do, on_retry=on_retry)

    def _parse_job(self, item: dict) -> Job | None:
        job_id = str(item.get("id") or "")
        if not job_id:
            log.warning("Uber job item has no id, skipping: %s", item.get("title", "?"))
            return None
        url = f"https://www.uber.com/careers/apply/form/{job_id}"
        return Job(
            id=job_id,
            title=item.get("title", ""),
            location=_build_location(item),
            url=url,
            apply_url=url,
            published_at=_parse_date(item.get("creationDate")),
            last_listing_update=_parse_date(item.get("updatedDate")),
            department=item.get("department") or None,
            team=item.get("team") or None,
            description=item.get("description") or None,
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        data = self._fetch_page(on_retry=on_retry)
        inner = data.get("data", {})
        results = inner.get("results", [])
        total_obj = inner.get("totalResults", {})
        # totalResults.low is a declared lower bound — treat it as a hint only;
        # keep fetching until we get an empty page so we don't miss late-added jobs.
        total = (
            total_obj.get("low", len(results))
            if isinstance(total_obj, dict)
            else len(results)
        )

        jobs: JobList = [j for j in (self._parse_job(i) for i in results) if j is not None]
        if on_progress:
            on_progress(len(jobs), total)

        if len(jobs) >= total:
            return jobs

        remaining_offsets = list(range(len(jobs), total, PAGE_SIZE))
        lock = threading.Lock()

        def _fetch_remaining(offset: int) -> list[Job]:
            page = self._fetch_page(offset, on_retry=on_retry)
            raw = page.get("data", {}).get("results", [])
            return [j for j in (self._parse_job(i) for i in raw) if j is not None]

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_fetch_remaining, off): off for off in remaining_offsets}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                return j
        raise ValueError(f"Job ID {job_id} not found on Uber careers.")
