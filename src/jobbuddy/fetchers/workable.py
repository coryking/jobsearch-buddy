"""Workable ATS fetcher.

Uses two public APIs:
- v1 widget: list all jobs (no descriptions)
- v2: single job detail with full description

list_jobs() enriches each job with its v2 description using concurrent requests.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

_V1_BASE = "https://apply.workable.com/api/v1/widget/accounts"
_V2_BASE = "https://apply.workable.com/api/v2/accounts"


class WorkableFetcher(ATSFetcher):
    ats_type = "workable"

    def _build_location_v1(self, j: dict) -> str:
        """Build location string from v1 widget job data."""
        parts = [p for p in [j.get("city"), j.get("state"), j.get("country")] if p]
        return ", ".join(parts)

    def _parse_v2_job(self, j: dict) -> Job:
        """Parse a v2 API response into a Job with full description."""
        location_parts = []
        loc = j.get("location", {})
        for key in ("city", "region", "country"):
            if loc.get(key):
                location_parts.append(loc[key])
        location = ", ".join(location_parts)

        workplace = j.get("workplace", "")
        if workplace == "remote":
            location = f"Remote — {location}" if location else "Remote"

        description_html = j.get("description", "")
        requirements_html = j.get("requirements", "")
        benefits_html = j.get("benefits", "")

        sections = []
        if description_html:
            sections.append(strip_html(description_html))
        if requirements_html:
            sections.append("Requirements\n" + strip_html(requirements_html))
        if benefits_html:
            sections.append("Benefits\n" + strip_html(benefits_html))
        description = "\n\n".join(sections) if sections else None

        dept = j.get("department", [])
        department = dept[0] if isinstance(dept, list) and dept else dept if isinstance(dept, str) else None

        published = j.get("published")
        published_at = published[:10] if published else None

        return Job(
            id=j["shortcode"],
            title=j["title"],
            location=location,
            url=f"https://apply.workable.com/{self.board}/j/{j['shortcode']}/",
            apply_url=f"https://apply.workable.com/{self.board}/j/{j['shortcode']}/apply/",
            published_at=published_at,
            department=department,
            description=description,
        )

    def _fetch_v2_detail(self, shortcode: str) -> Job | None:
        """Fetch a single job's v2 detail. Returns None on failure."""
        try:
            resp = self.client.get(f"{_V2_BASE}/{self.board}/jobs/{shortcode}")
            resp.raise_for_status()
            return self._parse_v2_job(resp.json())
        except Exception as exc:
            log.warning("Failed to fetch v2 detail for %s: %s", shortcode, exc)
            return None

    def list_jobs(self) -> list[Job]:
        # Get the job list from v1 (fast, no descriptions)
        resp = self.client.get(f"{_V1_BASE}/{self.board}")
        resp.raise_for_status()
        v1_jobs = resp.json().get("jobs", [])

        if not v1_jobs:
            return []

        # Enrich each job with v2 description in parallel
        shortcodes = [j["shortcode"] for j in v1_jobs]
        v2_by_id: dict[str, Job] = {}

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(self._fetch_v2_detail, sc): sc for sc in shortcodes}
            for future in as_completed(futures):
                sc = futures[future]
                job = future.result()
                if job:
                    v2_by_id[sc] = job

        # Return v2 jobs where available, fall back to v1 stubs
        jobs = []
        for j in v1_jobs:
            sc = j["shortcode"]
            if sc in v2_by_id:
                jobs.append(v2_by_id[sc])
            else:
                jobs.append(
                    Job(
                        id=sc,
                        title=j["title"],
                        location=self._build_location_v1(j),
                        url=j.get("url", ""),
                        apply_url=j.get("application_url", ""),
                        published_at=j.get("published_on"),
                        department=j.get("department"),
                    )
                )
        return jobs

    def fetch_job(self, job_id: str) -> Job:
        resp = self.client.get(f"{_V2_BASE}/{self.board}/jobs/{job_id}")
        if resp.status_code == 404:
            raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
        resp.raise_for_status()
        return self._parse_v2_job(resp.json())
