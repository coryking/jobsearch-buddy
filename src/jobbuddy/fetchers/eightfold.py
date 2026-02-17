"""Eightfold AI ATS fetcher."""

import time
from datetime import datetime, timezone

import httpx

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html

PAGE_SIZE = 10  # Eightfold returns exactly 10 per page, not configurable
PAGE_DELAY = 0.3  # seconds between paginated requests

# Browser-like headers to avoid CloudFront blocks
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
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


class EightfoldFetcher(ATSFetcher):
    ats_type = "eightfold"
    descriptions_in_listing = False

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
        self._headers = {**_HEADERS, "Referer": f"{self.base_url}/careers"}

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
        )

    def list_jobs(self) -> list[Job]:
        self._require_config()
        params = self._build_search_params()
        jobs: list[Job] = []
        start = 0

        while True:
            params["start"] = start
            resp = httpx.get(self._api_url("search"), params=params, headers=self._headers, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", {})

            count = data.get("count", 0)
            positions = data.get("positions", [])
            for pos in positions:
                jobs.append(self._position_to_job(pos))

            start += PAGE_SIZE
            if start >= count:
                break
            time.sleep(PAGE_DELAY)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        params = {
            "position_id": job_id,
            "domain": self.domain,
            "hl": "en",
        }
        resp = httpx.get(self._api_url("position_details"), params=params, headers=self._headers, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", {})

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
        )
