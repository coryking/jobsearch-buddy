"""Tesla careers fetcher (tesla.com/careers).

Tesla's careers site is a React SPA backed by two API endpoints:
  - GET /cua-api/apps/careers/state — all listings (~6K) in one response,
    with compact IDs resolved via lookup tables (no descriptions)
  - GET /cua-api/careers/job/{id} — full job detail with HTML descriptions

Descriptions are NOT included in listings, so this is a stub fetcher
that requires enrichment.

Tesla uses Akamai Bot Manager. Plain httpx requests get 403. The sync
pipeline will retry on transient errors, but persistent 403s mean the
Akamai challenge isn't being solved. If this happens, you'll need to
provide valid session cookies or use a browser-bootstrapped session.

Company registry fields:
  - ats: "tesla"
  - board: ignored (single global board)
"""

import logging

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

STATE_URL = "https://www.tesla.com/cua-api/apps/careers/state"
JOB_URL = "https://www.tesla.com/cua-api/careers/job/{job_id}"

TYPE_LABELS = {1: "Full-time", 2: "Part-time", 3: "Intern", 4: "Seasonal"}


def build_description(data: dict) -> str | None:
    """Combine Tesla's split description fields into one plain-text block."""
    sections = []
    for key, heading in [
        ("jobDescription", None),
        ("jobResponsibilities", "Responsibilities"),
        ("jobRequirements", "Requirements"),
        ("jobCompensationAndBenefits", "Compensation & Benefits"),
    ]:
        raw = data.get(key, "")
        if not raw:
            continue
        text = strip_html(raw)
        if not text.strip():
            continue
        if heading:
            sections.append(f"{heading}:\n{text}")
        else:
            sections.append(text)

    return "\n\n".join(sections) if sections else None


def build_url(data: dict) -> str:
    """Build canonical Tesla careers URL from a job detail response."""
    path = data.get("url", "")
    if path:
        return f"https://www.tesla.com{path}"
    return f"https://www.tesla.com/careers/search/job/{data.get('id', '')}"


class TeslaFetcher(ATSFetcher):
    ats_type = "tesla"
    descriptions_in_listing = False
    enrich_delay = 0.1

    def __init__(self, board: str, name: str | None = None):
        super().__init__(board, name or "Tesla")
        self.client.headers.update({
            "Referer": "https://www.tesla.com/careers/search/",
            "Accept": "application/json",
        })

    def _fetch_state(self, *, on_retry: RetryCallback | None = None) -> dict:
        def do_fetch():
            resp = self.client.get(STATE_URL)
            resp.raise_for_status()
            return resp.json()

        return self._retry_request(do_fetch, on_retry=on_retry)

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        state = self._fetch_state(on_retry=on_retry)

        lookup_locations = state.get("lookup", {}).get("locations", {})
        lookup_departments = state.get("lookup", {}).get("departments", {})
        listings = state.get("listings", [])
        total = len(listings)

        jobs: JobList = []
        for item in listings:
            job_id = str(item.get("id", ""))
            title = item.get("t", "")
            dept_id = str(item.get("dp", ""))
            loc_id = str(item.get("l", ""))
            type_id = item.get("y")

            location = lookup_locations.get(loc_id, "")
            department = lookup_departments.get(dept_id)

            jobs.append(Job(
                id=job_id,
                title=title,
                location=location,
                url=f"https://www.tesla.com/careers/search/job/{job_id}",
                apply_url=f"https://tesla.avature.net/Careers/ApplicationMethods?jobId={job_id}",
                department=department,
                team=department,
                ats_metadata={"timeType": TYPE_LABELS[type_id]} if type_id in TYPE_LABELS else None,
            ))

        if on_progress:
            on_progress(len(jobs), total)

        return jobs

    def _fetch_detail(self, job_id: str, *, on_retry: RetryCallback | None = None) -> dict:
        def do_fetch():
            resp = self.client.get(JOB_URL.format(job_id=job_id))
            resp.raise_for_status()
            return resp.json()

        return self._retry_request(do_fetch, on_retry=on_retry)

    def fetch_job(self, job_id: str) -> Job:
        data = self._fetch_detail(job_id)
        return self._detail_to_job(data, job_id)

    def _detail_to_job(self, data: dict, fallback_id: str = "") -> Job:
        job_id = str(data.get("id", fallback_id))
        return Job(
            id=job_id,
            title=data.get("title", ""),
            location=data.get("location", ""),
            url=build_url(data),
            apply_url=data.get("applyUrl", f"https://tesla.avature.net/Careers/ApplicationMethods?jobId={job_id}"),
            department=data.get("department"),
            team=data.get("jobFamily"),
            description=build_description(data),
        )

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        return build_description(self._fetch_detail(job_id))
