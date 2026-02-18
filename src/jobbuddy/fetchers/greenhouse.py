"""Greenhouse ATS fetcher."""

import httpx

from jobbuddy.fetchers.base import ATSFetcher, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html


class GreenhouseFetcher(ATSFetcher):
    ats_type = "greenhouse"

    def resolve_name(self) -> str | None:
        """Fetch company display name from Greenhouse board API."""
        try:
            resp = self.client.get(f"https://boards-api.greenhouse.io/v1/boards/{self.board}")
            resp.raise_for_status()
            return resp.json().get("name")
        except (httpx.HTTPError, KeyError):
            return None

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> list[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs?content=true"
        resp = self.client.get(url)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobs", []):
            published = j.get("first_published")
            if published:
                published = published[:10]

            department = None
            for m in j.get("departments", []):
                department = m.get("name")
                break

            content = j.get("content", "")
            description = strip_html(content) if content else None

            jobs.append(
                Job(
                    id=str(j["id"]),
                    title=j["title"],
                    location=j.get("location", {}).get("name", ""),
                    url=j.get("absolute_url", ""),
                    apply_url=j.get("absolute_url", ""),
                    published_at=published,
                    department=department,
                    team=department,
                    description=description,
                )
            )
        return jobs

    def fetch_job(self, job_id: str) -> Job:
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
