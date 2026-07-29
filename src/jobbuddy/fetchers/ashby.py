"""Ashby ATS fetcher."""

import re
from typing import Any

import httpx

from jobbuddy.fetchers.base import (
    ApplicationFormNotSupported,
    ATSFetcher,
    ProgressCallback,
    RetryCallback,
)
from jobbuddy.models import Job


_APPLICATION_FORM_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, "
    "$jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, "
    "jobPostingId: $jobPostingId) { "
    "id title applicationForm { sections { title fieldEntries { isRequired field } } } "
    "} }"
)


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

            # Ashby splits multi-location postings across `location` (primary)
            # and `secondaryLocations`. Join them the way the hosted page
            # renders them ("City A; City B; ...") so no site is dropped.
            locations = [j.get("location", "")]
            locations += [
                s.get("location", "")
                for s in j.get("secondaryLocations") or []
            ]
            location = "; ".join(loc for loc in locations if loc)

            jobs.append(
                Job(
                    id=j["id"],
                    title=j["title"],
                    location=location,
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

    def fetch_application_form(self, board: str, job_id: str) -> dict[str, Any]:
        """Return the raw `applicationForm` subtree from Ashby's GraphQL API.

        The JD itself is already available via `list_jobs`/`fetch_job`, so
        only the form structure is returned here — `sections[].fieldEntries[]`
        with `field` metadata and `isRequired` flags. Raises
        ApplicationFormNotSupported if the posting or its form is missing.
        """
        url = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
        body = {
            "operationName": "ApiJobPosting",
            "variables": {
                "organizationHostedJobsPageName": board,
                "jobPostingId": job_id,
            },
            "query": _APPLICATION_FORM_QUERY,
        }
        resp = self.client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        posting = (data.get("data") or {}).get("jobPosting")
        if not posting:
            raise ApplicationFormNotSupported(
                f"Ashby returned no posting for {board}/{job_id}"
            )
        form = posting.get("applicationForm")
        if not form:
            raise ApplicationFormNotSupported(
                f"Ashby posting {board}/{job_id} has no application form"
            )
        return form
