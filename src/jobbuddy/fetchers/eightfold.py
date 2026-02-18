"""Eightfold AI ATS fetcher."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 10  # Eightfold returns exactly 10 per page, not configurable


class EightfoldFetcher(ATSFetcher):
    ats_type = "eightfold"
    descriptions_in_listing = False
    enrich_delay = 0.0
    max_retries = 5

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        base_url: str = "",
        domain: str = "",
        default_filters: dict | None = None,
    ):
        super().__init__(board, name)
        self.base_url = base_url.rstrip("/")
        self.domain = domain
        self.default_filters = default_filters or {}
        self.client.headers["Referer"] = f"{self.base_url}/careers"

    def _require_config(self) -> None:
        if not self.base_url or not self.domain:
            raise ValueError(
                f"Eightfold fetcher for '{self.board}' requires base_url and domain. "
                "These are normally set from the company registry."
            )

    def _api_url(self, endpoint: str) -> str:
        return f"{self.base_url}/api/pcsx/{endpoint}"

    def _build_search_params(self) -> dict:
        params: dict = {
            "domain": self.domain,
            "sort_by": "timestamp",
        }
        # Keys are passed through as-is — the registry uses the exact API param names
        # (e.g. "location" is a top-level param, "filter_career_discipline" is a filter)
        params.update(self.default_filters)
        return params

    @staticmethod
    def _extract_metadata(data: dict) -> dict:
        """Pull interesting Eightfold fields into ats_metadata."""
        meta: dict = {}
        for key in ("displayJobId", "workLocationOption"):
            if data.get(key):
                meta[key] = data[key]
        # Detail-only fields (lists of strings)
        for key in ("efcustomTextWorkSite", "efcustomTextRoletype", "efcustomTextEmploymentType"):
            val = data.get(key)
            if val:
                meta[key] = val[0] if isinstance(val, list) and len(val) == 1 else val
        return meta or None

    def _position_to_job(self, pos: dict) -> Job:
        """Map a slim position object from search results to a Job."""
        pos_id = str(pos["id"])
        locations = pos.get("standardizedLocations") or pos.get("locations") or []
        posted_ts = pos.get("postedTs")
        published_at = None
        if posted_ts:
            published_at = datetime.fromtimestamp(posted_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        url = f"{self.base_url}/careers/job/{pos_id}"
        return Job(
            id=pos_id,
            title=pos.get("name", ""),
            location=" | ".join(locations),
            url=url,
            apply_url=url,
            published_at=published_at,
            department=pos.get("department"),
            ats_metadata=self._extract_metadata(pos),
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        self._require_config()
        base_params = self._build_search_params()

        # Fetch page 0 to learn count (with retry)
        def _fetch_page0():
            resp = self.client.get(self._api_url("search"), params={**base_params, "start": 0})
            resp.raise_for_status()
            return resp.json().get("data", {})

        data = self._retry_request(_fetch_page0, on_retry=on_retry)
        count = data.get("count", 0)
        jobs: JobList = [self._position_to_job(pos) for pos in data.get("positions", [])]
        if on_progress:
            on_progress(len(jobs), count)

        remaining_starts = list(range(PAGE_SIZE, count, PAGE_SIZE))
        if not remaining_starts:
            return jobs

        lock = threading.Lock()

        def _fetch_page(start: int) -> list[Job]:
            def _do_fetch():
                page_resp = self.client.get(self._api_url("search"), params={**base_params, "start": start})
                page_resp.raise_for_status()
                return [self._position_to_job(pos) for pos in page_resp.json().get("data", {}).get("positions", [])]
            return self._retry_request(_do_fetch, on_retry=on_retry)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_page, s): s for s in remaining_starts}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, count)

        return jobs

    def _fetch_detail(self, job_id: str) -> dict:
        """Fetch position_details (no retry — base class handles 429 retries)."""
        params = {
            "position_id": job_id,
            "domain": self.domain,
            "hl": "en",
        }
        resp = self.client.get(self._api_url("position_details"), params=params)
        resp.raise_for_status()
        return resp.json().get("data", {})

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        data = self._fetch_detail(job_id)

        if not data:
            raise ValueError(f"Position {job_id} not found.")

        locations = data.get("standardizedLocations") or data.get("locations") or []
        posted_ts = data.get("postedTs")
        published_at = None
        if posted_ts:
            published_at = datetime.fromtimestamp(posted_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        url = f"{self.base_url}/careers/job/{job_id}"
        description = data.get("jobDescription", "")
        if description:
            description = strip_html(description)

        return Job(
            id=str(data.get("id", job_id)),
            title=data.get("name", ""),
            location=" | ".join(locations),
            url=url,
            apply_url=url,
            published_at=published_at,
            department=data.get("department"),
            description=description or None,
            ats_metadata=self._extract_metadata(data),
        )

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description directly from detail API without building a full Job."""
        self._require_config()
        data = self._fetch_detail(job_id)
        desc = data.get("jobDescription", "")
        return strip_html(desc) if desc else None
