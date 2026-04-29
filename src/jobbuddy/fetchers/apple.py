"""Apple careers fetcher (jobs.apple.com)."""

import json
import logging
import re

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

PAGE_SIZE = 20  # Apple returns 20 results per SSR page


def build_location(loc: dict) -> str:
    """Build a location string from Apple's location object.

    Level 2+ locations have city/state/country. Level 1 is country-only.
    """
    parts = [p for p in [loc.get("city"), loc.get("stateProvince"), loc.get("countryName")] if p]
    return ", ".join(parts) if parts else loc.get("name", "")


def parse_ssr_html(html: str) -> tuple[list[dict], int]:
    """Extract search results and total count from Apple SSR hydration data.

    Returns (search_results, total_records).
    Raises ValueError if hydration data not found.
    """
    match = re.search(
        r'window\.__staticRouterHydrationData = JSON\.parse\("(.+?)"\);',
        html,
    )
    if not match:
        raise ValueError("Could not find hydration data in Apple careers HTML")

    raw = match.group(1)
    unescaped = raw.encode().decode("unicode_escape")
    data = json.loads(unescaped)

    search_data = data["loaderData"]["search"]
    results = search_data["searchResults"]
    total = search_data["totalRecords"]
    return results, total


def build_description(data: dict) -> str | None:
    """Combine jobSummary + description from a detail API response."""
    summary = data.get("jobSummary", "")
    raw_desc = data.get("description", "")
    desc_text = strip_html(raw_desc) if raw_desc else ""

    if summary and desc_text:
        return f"{summary}\n\n{desc_text}"
    return summary or desc_text or None


class AppleFetcher(ATSFetcher):
    ats_type = "apple"
    descriptions_in_listing = False
    enrich_delay = 0.2

    def _result_to_job(self, result: dict) -> Job:
        """Map an SSR search result to a Job."""
        position_id = result["positionId"]
        slug = result.get("transformedPostingTitle", "")
        locations = result.get("locations") or []
        location_str = " | ".join(build_location(loc) for loc in locations)

        team_obj = result.get("team") or {}
        team_name = team_obj.get("teamName")

        post_date = result.get("postDateInGMT", "")
        published_at = post_date[:10] if post_date else None

        url = f"https://jobs.apple.com/en-us/details/{position_id}/{slug}"

        return Job(
            id=position_id,
            title=result.get("postingTitle", ""),
            location=location_str,
            url=url,
            apply_url=url,
            published_at=published_at,
            department=team_name,
            team=team_name,
            description=result.get("jobSummary"),
        )

    def _fetch_ssr_page(self, page: int, *, on_retry: RetryCallback | None = None) -> tuple[list[dict], int]:
        """Fetch a single SSR page and return (results, total)."""
        def do_fetch():
            resp = self.client.get(
                f"https://jobs.apple.com/en-us/search?sort=newest&page={page}",
            )
            resp.raise_for_status()
            return parse_ssr_html(resp.text)

        return self._retry_request(do_fetch, on_retry=on_retry)

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        # Fetch page 1
        results, total = self._fetch_ssr_page(1, on_retry=on_retry)
        jobs: JobList = [self._result_to_job(r) for r in results]
        if on_progress:
            on_progress(len(jobs), total)

        # Paginate remaining pages
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        for page in range(2, total_pages + 1):
            page_results, _ = self._fetch_ssr_page(page, on_retry=on_retry)
            jobs.extend(self._result_to_job(r) for r in page_results)
            if on_progress:
                on_progress(len(jobs), total)

        return jobs

    def _fetch_detail(self, job_id: str) -> dict:
        """Fetch job detail from the Apple API."""
        resp = self.client.get(
            f"https://jobs.apple.com/api/v1/jobDetails/{job_id}",
            params={"locale": "en-us"},
            headers={"locale": "en-us"},
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("res", body)

    def _detail_to_job(self, data: dict) -> Job:
        """Map a detail API response to a Job."""
        position_id = data.get("jobNumber") or data["positionId"]
        slug = data.get("transformedPostingTitle", "")
        locations = data.get("locations") or []
        location_str = " | ".join(build_location(loc) for loc in locations)

        team_names = data.get("teamNames") or []
        team_name = team_names[0] if team_names else None

        post_date = data.get("postDateInGMT", "")
        published_at = post_date[:10] if post_date else None

        url = f"https://jobs.apple.com/en-us/details/{position_id}/{slug}"

        return Job(
            id=position_id,
            title=data.get("postingTitle", ""),
            location=location_str,
            url=url,
            apply_url=url,
            published_at=published_at,
            department=team_name,
            team=team_name,
            description=build_description(data),
        )

    def fetch_job(self, job_id: str) -> Job:
        data = self._fetch_detail(job_id)
        return self._detail_to_job(data)

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description directly from detail API without building a full Job."""
        return build_description(self._fetch_detail(job_id))
