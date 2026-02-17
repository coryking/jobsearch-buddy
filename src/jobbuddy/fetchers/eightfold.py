"""Eightfold AI ATS fetcher."""

import logging
import time
from datetime import datetime, timezone

import httpx

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 10  # Eightfold returns exactly 10 per page, not configurable
PAGE_DELAY = 0.3  # seconds between paginated requests
ENRICH_DELAY = 0.5  # seconds between individual description fetches
BACKOFF_BASE = 2.0  # base seconds for exponential backoff on 429
MAX_RETRIES = 5  # max retries per request on 429

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

    def _fetch_detail(self, job_id: str) -> dict:
        """Fetch position_details with exponential backoff on 429."""
        params = {
            "position_id": job_id,
            "domain": self.domain,
            "hl": "en",
        }
        for attempt in range(MAX_RETRIES + 1):
            resp = httpx.get(
                self._api_url("position_details"),
                params=params,
                headers=self._headers,
                timeout=30,
            )
            if resp.status_code == 429:
                if attempt == MAX_RETRIES:
                    resp.raise_for_status()
                # Use Retry-After header if present, otherwise exponential backoff
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = BACKOFF_BASE * (2 ** attempt)
                log.info("429 for job %s, backing off %.1fs (attempt %d/%d)",
                         job_id, wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("data", {})
        return {}  # unreachable, but satisfies type checker

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
        )

    def fetch_descriptions(self, job_ids: list[str]) -> dict[str, str | None]:
        """Fetch descriptions one at a time with polite delays and 429 backoff."""
        self._require_config()
        results: dict[str, str | None] = {}
        for job_id in job_ids:
            try:
                data = self._fetch_detail(job_id)
                desc = data.get("jobDescription", "")
                results[job_id] = strip_html(desc) if desc else None
            except Exception as e:
                log.warning("Failed to fetch description for %s: %s", job_id, e)
                results[job_id] = None
            time.sleep(ENRICH_DELAY)
        return results
