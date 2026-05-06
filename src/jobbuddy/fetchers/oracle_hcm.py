"""Oracle HCM Cloud (Oracle Recruiting Cloud) fetcher."""

from jobbuddy.fetchers.base import ATSFetcher, ProgressCallback, RetryCallback
from jobbuddy.models import Job, parse_published_at, strip_html

PAGE_SIZE = 25
MAX_RESULTS = 75  # cap per keyword query — relevancy drops fast past ~50 results


class OracleHCMFetcher(ATSFetcher):
    ats_type = "oracle_hcm"
    descriptions_in_listing = False
    enrich_delay = 0.0

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        ohcm_region: str = "",
        site_number: str = "CX_1",
        site_slug: str = "jobsearch",
        default_filters: dict | None = None,
    ):
        super().__init__(board, name)
        self.ohcm_region = ohcm_region
        self.site_number = site_number
        self.site_slug = site_slug
        self.default_filters = default_filters or {}

    @property
    def _base_url(self) -> str:
        if self.ohcm_region:
            return f"https://{self.board}.fa.{self.ohcm_region}.oraclecloud.com"
        return f"https://{self.board}.fa.oraclecloud.com"

    def _job_url(self, job_id: str) -> str:
        return f"{self._base_url}/hcmUI/CandidateExperience/en/sites/{self.site_slug}/job/{job_id}"

    def _list_api_url(
        self, keyword: str, location: str, offset: int, *, category: str = ""
    ) -> str:
        # Oracle finder syntax: finder=findReqs;param1=val1,param2=val2
        # Semicolon separates finder name from params, commas separate params
        finder_params = [f"siteNumber={self.site_number}"]
        if keyword:
            finder_params.append(f"keyword={keyword}")
        if location:
            finder_params.append(f"location={location}")
        if category:
            finder_params.append(f"selectedCategoriesFacet={category}")
            finder_params.append("lastSelectedFacet=CATEGORIES")
        # limit and offset must be finder params — the API ignores them as query params
        finder_params.append(f"limit={PAGE_SIZE}")
        finder_params.append(f"offset={offset}")
        finder = ",".join(finder_params)
        return (
            f"{self._base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?finder=findReqs;{finder}"
            f"&onlyData=true&expand=requisitionList"
        )

    def _detail_api_url(self, job_id: str) -> str:
        return (
            f"{self._base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?finder=ById;Id={job_id},siteNumber={self.site_number}"
            f"&onlyData=true&expand=all"
        )

    def _list_item_to_job(self, item: dict) -> Job:
        job_id = str(item.get("Id", ""))
        return Job(
            id=job_id,
            title=item.get("Title", ""),
            location=item.get("PrimaryLocation", ""),
            url=self._job_url(job_id),
            apply_url=self._job_url(job_id),
            published_at=parse_published_at(item.get("PostedDate")),
            department=item.get("Category"),
        )

    def _fetch_query(
        self, keyword: str, location: str, *, category: str = ""
    ) -> list[Job]:
        """Fetch jobs for a single query (keyword and/or category).

        Keyword queries cap at MAX_RESULTS (relevancy drops fast).
        Category queries fetch all results (exact match, no relevancy decay).
        """
        max_results = None if category else MAX_RESULTS
        jobs: list[Job] = []
        offset = 0
        total = None

        while True:
            url = self._list_api_url(keyword, location, offset, category=category)
            resp = self.client.get(url)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            if not items:
                break

            if total is None:
                total = items[0].get("TotalJobsCount", 0)

            for item in items:
                for req in item.get("requisitionList", []):
                    jobs.append(self._list_item_to_job(req))

            offset += PAGE_SIZE
            if total is not None and offset >= total:
                break
            if max_results and len(jobs) >= max_results:
                break

        return jobs

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> list[Job]:
        location = self.default_filters.get("location", "")

        # keywords can be a list (multiple queries merged + deduped) or a single string
        keywords_raw = self.default_filters.get("keywords") or self.default_filters.get("keyword", "")
        if isinstance(keywords_raw, str):
            keywords = [keywords_raw] if keywords_raw else [""]
        else:
            keywords = list(keywords_raw)

        # categories filter by Oracle HCM category facet IDs
        categories_raw = self.default_filters.get("categories", [])
        if isinstance(categories_raw, str):
            categories = [categories_raw]
        else:
            categories = list(categories_raw)

        seen_ids: set[str] = set()
        jobs: list[Job] = []

        def _collect(new_jobs: list[Job]) -> None:
            for job in new_jobs:
                if job.id not in seen_ids:
                    seen_ids.add(job.id)
                    jobs.append(job)

        if categories:
            # Query per category (each may be combined with each keyword)
            for cat in categories:
                for kw in keywords:
                    _collect(self._fetch_query(kw, location, category=cat))
        else:
            for kw in keywords:
                _collect(self._fetch_query(kw, location))

        return jobs

    def _fetch_detail_data(self, job_id: str) -> dict:
        """Fetch detail API response for a single job."""
        url = self._detail_api_url(job_id)
        resp = self.client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            raise ValueError(f"Job {job_id} not found on Oracle HCM board '{self.board}'.")
        return items[0]

    def _parse_detail(self, detail: dict, job_id: str) -> Job:
        """Parse a detail API item into a Job."""
        desc_parts = []
        for field in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"):
            val = detail.get(field)
            if val:
                desc_parts.append(strip_html(val))
        description = "\n\n".join(desc_parts) or None

        posted = detail.get("ExternalPostedStartDate") or detail.get("PostedDate")

        job_url = self._job_url(job_id)
        return Job(
            id=str(detail.get("Id", job_id)),
            title=detail.get("Title", ""),
            location=detail.get("PrimaryLocation", ""),
            url=job_url,
            apply_url=job_url,
            published_at=parse_published_at(posted),
            department=detail.get("Category"),
            description=description,
        )

    def fetch_job(self, job_id: str) -> Job:
        detail = self._fetch_detail_data(job_id)
        return self._parse_detail(detail, job_id)

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description from detail API without building a full Job."""
        detail = self._fetch_detail_data(job_id)
        desc_parts = []
        for field in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"):
            val = detail.get(field)
            if val:
                desc_parts.append(strip_html(val))
        return "\n\n".join(desc_parts) or None
