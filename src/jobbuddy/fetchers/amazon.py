"""Amazon.jobs fetcher.

Uses the POST /api/jobs/search endpoint. No authentication required.
Descriptions are included in listing results (full fetcher).

Company registry fields:
  - ats: "amazon"
  - board: ignored (single global board)
  - categories: list of category names to filter (e.g. ["Software Development"])
  - country: optional ISO-2 country code filter (e.g. "US")
"""

import logging
import re
from datetime import date, datetime, timezone

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

SEARCH_URL = "https://amazon.jobs/api/jobs/search"
PAGE_SIZE = 500


def _parse_epoch(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (ValueError, OSError):
        return None


def _build_location(fields: dict) -> str:
    parts = []
    city = _first(fields.get("city"))
    region = _first(fields.get("region"))
    country = _first(fields.get("country"))
    if city:
        parts.append(city)
    if region:
        parts.append(region)
    if country:
        parts.append(country)
    return ", ".join(parts)


def _first(values: list | None) -> str | None:
    if values:
        return values[0]
    return None


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return slug.strip("-")


class AmazonFetcher(ATSFetcher):
    ats_type = "amazon"

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        categories: list[str] | None = None,
        country: str = "",
    ):
        super().__init__(board, name or "Amazon")
        self.categories = categories or []
        self.country = country
        self.client.headers.update({
            "Origin": "https://amazon.jobs",
            "Referer": "https://amazon.jobs/",
        })

    def _build_request_body(self, start: int, size: int) -> dict:
        filter_facets = []
        if self.categories:
            filter_facets.append({
                "name": "category",
                "requestedFacetCount": 9999,
                "values": [{"name": c} for c in self.categories],
            })
        if self.country:
            filter_facets.append({
                "name": "country",
                "requestedFacetCount": 9999,
                "values": [{"name": self.country.upper()}],
            })

        return {
            "accessLevel": "EXTERNAL",
            "contentFilterFacets": [],
            "excludeFacets": [
                {"name": "isConfidential", "values": [{"name": "1"}]},
                {"name": "businessCategory", "values": [{"name": "a-confidential-job"}]},
            ],
            "filterFacets": filter_facets,
            "includeFacets": [],
            "jobTypeFacets": [],
            "locationFacets": [[
                {"name": "country", "requestedFacetCount": 9999},
                {"name": "normalizedStateName", "requestedFacetCount": 9999},
                {"name": "normalizedCityName", "requestedFacetCount": 9999},
            ]],
            "query": "",
            "size": size,
            "start": start,
            "treatment": "OM",
            "sort": {"sortOrder": "DESCENDING", "sortType": "SCORE"},
        }

    def _parse_hit(self, hit: dict) -> Job:
        fields = hit.get("fields", {})
        job_id = _first(fields.get("icimsJobId")) or ""
        title = _first(fields.get("title")) or ""
        slug = _slugify_title(title)

        description_html = _first(fields.get("description")) or ""
        basic_quals = _first(fields.get("basicQualifications")) or ""
        preferred_quals = _first(fields.get("preferredQualifications")) or ""

        desc_parts = []
        if description_html:
            desc_parts.append(strip_html(description_html))
        if basic_quals:
            desc_parts.append("Basic Qualifications:\n" + strip_html(basic_quals))
        if preferred_quals:
            desc_parts.append("Preferred Qualifications:\n" + strip_html(preferred_quals))

        description = "\n\n".join(desc_parts) if desc_parts else None

        category = _first(fields.get("category"))
        job_family = _first(fields.get("jobFamily"))
        url_next_step = _first(fields.get("urlNextStep")) or ""

        return Job(
            id=job_id,
            title=title,
            location=_build_location(fields),
            url=f"https://www.amazon.jobs/en/jobs/{job_id}/{slug}",
            apply_url=url_next_step or f"https://account.amazon.jobs/jobs/{job_id}/apply",
            published_at=_parse_epoch(_first(fields.get("createdDate"))),
            last_listing_update=_parse_epoch(_first(fields.get("updatedDate"))),
            department=category,
            team=job_family,
            description=description,
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        def _fetch_page(start: int) -> dict:
            resp = self.client.post(
                SEARCH_URL,
                json=self._build_request_body(start, PAGE_SIZE),
                params={"is_als": "true"},
            )
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(lambda: _fetch_page(0), on_retry=on_retry)
        total = data.get("found", 0)
        hits = data.get("searchHits", [])
        jobs: JobList = [self._parse_hit(h) for h in hits]

        if on_progress:
            on_progress(len(jobs), total)

        start = len(hits)
        while start < total:
            page_data = self._retry_request(lambda s=start: _fetch_page(s), on_retry=on_retry)
            page_hits = page_data.get("searchHits", [])
            if not page_hits:
                break
            jobs.extend(self._parse_hit(h) for h in page_hits)
            start += len(page_hits)
            if on_progress:
                on_progress(len(jobs), total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        body = self._build_request_body(0, 10)
        body["query"] = job_id

        def _do_fetch():
            resp = self.client.post(SEARCH_URL, json=body, params={"is_als": "true"})
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(_do_fetch)

        for hit in data.get("searchHits", []):
            fields = hit.get("fields", {})
            if _first(fields.get("icimsJobId")) == job_id:
                return self._parse_hit(hit)

        raise ValueError(f"Job ID {job_id} not found on Amazon.")
