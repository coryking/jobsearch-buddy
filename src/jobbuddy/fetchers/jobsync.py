"""JobSync (DirectEmployers/Solr) ATS fetcher.

JobSync provides a Solr-based search API at prod-search-api.jobsyn.org that
fronts various ATS backends (commonly iCIMS). Results are scoped to a company
via the x-origin header. Page size is capped at 10 by the server.

Company registry fields:
  - ats: "jobsync"
  - board: company slug (e.g. "alaskaair")
  - origin_host: careers site hostname (e.g. "careers.alaskaair.com")
"""

import logging
from datetime import date

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job

log = logging.getLogger(__name__)

SEARCH_URL = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
PAGE_SIZE = 10


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class JobSyncFetcher(ATSFetcher):
    ats_type = "jobsync"

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        origin_host: str = "",
    ):
        super().__init__(board, name)
        self.origin_host = origin_host
        if self.origin_host:
            self.client.headers["x-origin"] = self.origin_host
            self.client.headers["Origin"] = f"https://{self.origin_host}"
            self.client.headers["Referer"] = f"https://{self.origin_host}/"

    def _require_config(self) -> None:
        if not self.origin_host:
            raise ValueError(
                f"JobSync fetcher for '{self.name}' requires origin_host. "
                "This is normally set from the company registry."
            )

    def _job_url(self, title_slug: str, guid: str) -> str:
        return f"https://{self.origin_host}/jobs/{title_slug}/{guid}/"

    def _parse_job(self, raw: dict) -> Job:
        guid = raw.get("guid", "")
        title_slug = raw.get("title_slug", "")
        return Job(
            id=guid,
            title=raw.get("title_exact", ""),
            location=raw.get("location_exact", ""),
            url=self._job_url(title_slug, guid),
            apply_url=self._job_url(title_slug, guid),
            published_at=_parse_date(raw.get("date_new")),
            team=raw.get("company_exact"),
            description=raw.get("description"),
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        self._require_config()
        seen_guids: set[str] = set()
        jobs: JobList = []

        def _add_jobs(raw_jobs: list[dict]) -> None:
            for raw in raw_jobs:
                guid = raw.get("guid", "")
                if guid and guid not in seen_guids:
                    seen_guids.add(guid)
                    jobs.append(self._parse_job(raw))

        def _fetch_page(page: int):
            resp = self.client.get(
                SEARCH_URL,
                params={"page": page, "num_items": PAGE_SIZE},
            )
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(lambda: _fetch_page(1), on_retry=on_retry)
        pagination = data.get("pagination", {})
        total = pagination.get("total", 0)

        _add_jobs(data.get("featured_jobs", []))
        _add_jobs(data.get("jobs", []))

        if on_progress:
            on_progress(len(jobs), total)

        total_pages = pagination.get("total_pages", 1)
        for page in range(2, total_pages + 1):
            page_data = self._retry_request(lambda p=page: _fetch_page(p), on_retry=on_retry)
            _add_jobs(page_data.get("featured_jobs", []))
            _add_jobs(page_data.get("jobs", []))
            if on_progress:
                on_progress(len(jobs), total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        self._require_config()
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.name}.")
