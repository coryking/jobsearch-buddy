"""Ashby ATS fetcher."""

import re

import httpx

from jobbuddy.fetchers.base import ATSFetcher, ProgressCallback, RetryCallback
from jobbuddy.models import Job


class AshbyFetcher(ATSFetcher):
    ats_type = "ashby"

    def resolve_name(self) -> str | None:
        """Fetch company display name from Ashby page title ("<Company> Jobs")."""
        try:
            resp = self.client.get(f"https://jobs.ashbyhq.com/{self.board}")
            resp.raise_for_status()
            m = re.search(r"<title>(.*?)</title>", resp.text)
            if m:
                name = m.group(1)
                if name.endswith(" Jobs"):
                    name = name[:-5]
                return name.strip() or None
        except httpx.HTTPError:
            pass
        return None

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> list[Job]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.board}?includeCompensation=true"
        resp = self.client.get(url)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobs", []):
            salary = None
            comp = j.get("compensation")
            if comp:
                salary = comp.get("compensationTierSummary")

            jobs.append(
                Job(
                    id=j["id"],
                    title=j["title"],
                    location=j.get("location", ""),
                    url=j.get("jobUrl", ""),
                    apply_url=j.get("applyUrl", ""),
                    published_at=j.get("publishedAt", "")[:10] if j.get("publishedAt") else None,
                    department=j.get("department"),
                    team=j.get("team"),
                    salary=salary,
                    description=j.get("descriptionPlain"),
                )
            )
        return jobs

    def fetch_job(self, job_id: str) -> Job:
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
