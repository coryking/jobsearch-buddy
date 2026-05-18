"""Greenhouse ATS fetcher."""

from typing import Any

import httpx

from jobbuddy.fetchers.base import ATSFetcher, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

# Top-level keys we keep from the `?questions=true` payload. Everything else
# (notably `content`, `metadata`, `departments`, `offices`) is noise for a
# form-preview consumer.
_APPLICATION_FORM_KEEP = frozenset({
    "id",
    "title",
    "questions",
    "demographic_questions",
    "location_questions",
    "compliance",
})


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

            updated = j.get("updated_at")
            if updated:
                updated = updated[:10]

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
                    last_listing_update=updated,
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

    def fetch_application_form(self, board: str, job_id: str) -> dict[str, Any]:
        """Return the Greenhouse job-with-questions payload, trimmed.

        The `?questions=true` flag adds `questions`, `demographic_questions`,
        `location_questions`, and `compliance` to the standard job response.
        We project to the form-relevant keys and drop the JD `content` and
        EEOC regulatory `description` HTML, which add no signal to a
        form-preview ("warn me about surprise questions"). Result is ~6KB
        instead of ~29KB.
        """
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"
        resp = self.client.get(url)
        resp.raise_for_status()
        payload = resp.json()

        trimmed = {k: payload[k] for k in _APPLICATION_FORM_KEEP if k in payload}

        # `compliance` entries carry regulatory poster HTML in `description`.
        # The questions list inside each block is the part that matters.
        compliance = trimmed.get("compliance")
        if isinstance(compliance, list):
            trimmed["compliance"] = [
                {k: v for k, v in entry.items() if k != "description"}
                for entry in compliance
                if isinstance(entry, dict)
            ]
        return trimmed
