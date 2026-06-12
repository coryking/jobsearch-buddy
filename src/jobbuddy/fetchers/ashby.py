"""Ashby ATS fetcher."""

import re
import time
from typing import Any

import httpx

from jobbuddy.fetchers.base import (
    ApplicationFormNotSupported,
    ATSFetcher,
    ProgressCallback,
    RetryCallback,
)
from jobbuddy.models import Job, strip_html


_GRAPHQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"

_JOB_BOARD_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { "
    "jobBoard: jobBoardWithTeams("
    "organizationHostedJobsPageName: $organizationHostedJobsPageName) { "
    "teams { id name } "
    "jobPostings { id title teamId locationName employmentType "
    "compensationTierSummary } "
    "} }"
)

_JOB_POSTING_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, "
    "$jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, "
    "jobPostingId: $jobPostingId) { "
    "id title departmentName locationName descriptionHtml "
    "compensationTierSummary publishedDate "
    "} }"
)

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

    # Pacing between GraphQL detail calls on the posting-API-disabled
    # fallback path; the non-user-graphql endpoint 429s under bursts
    # (observed live: 4 req/s trips it even with backoff retries).
    graphql_detail_delay: float = 1.0

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
        if resp.status_code == 404:
            # Some boards disable the public posting API while the hosted
            # board at jobs.ashbyhq.com/<board> stays live (e.g. Whatnot,
            # EvenUp). The hosted board's GraphQL endpoint still serves
            # the postings, so a posting-API 404 means "fetch via
            # GraphQL", not "company left Ashby".
            return self._list_jobs_graphql(on_progress=on_progress, on_retry=on_retry)
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

    def _graphql(
        self,
        op: str,
        query: str,
        variables: dict,
        *,
        on_retry: RetryCallback | None = None,
    ) -> dict:
        def _post() -> dict:
            resp = self.client.post(
                f"{_GRAPHQL_URL}?op={op}",
                json={"operationName": op, "variables": variables, "query": query},
            )
            resp.raise_for_status()
            return resp.json().get("data") or {}

        return self._retry_request(_post, on_retry=on_retry)

    def _list_jobs_graphql(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> list[Job]:
        """List jobs via the hosted job board's GraphQL endpoint.

        The board listing carries only id/title/team/location, so each
        posting needs one detail call for description, published date,
        department, and compensation summary. Detail calls are paced —
        the endpoint 429s under rapid-fire requests.
        """
        data = self._graphql(
            "ApiJobBoardWithTeams",
            _JOB_BOARD_QUERY,
            {"organizationHostedJobsPageName": self.board},
            on_retry=on_retry,
        )
        board = data.get("jobBoard")
        if board is None:
            raise ValueError(
                f"Ashby board {self.board} not found via posting API or hosted job board"
            )
        teams = {t["id"]: t["name"] for t in board.get("teams") or []}
        postings = board.get("jobPostings") or []
        jobs = []
        for p in postings:
            if jobs and self.graphql_detail_delay:
                time.sleep(self.graphql_detail_delay)
            detail = self._graphql(
                "ApiJobPosting",
                _JOB_POSTING_QUERY,
                {
                    "organizationHostedJobsPageName": self.board,
                    "jobPostingId": p["id"],
                },
                on_retry=on_retry,
            ).get("jobPosting") or {}
            description_html = detail.get("descriptionHtml")
            published = detail.get("publishedDate") or ""
            url = f"https://jobs.ashbyhq.com/{self.board}/{p['id']}"
            jobs.append(
                Job(
                    id=p["id"],
                    title=p["title"],
                    location=p.get("locationName") or "",
                    url=url,
                    apply_url=f"{url}/application",
                    published_at=published[:10] if published else None,
                    department=detail.get("departmentName"),
                    team=teams.get(p.get("teamId")),
                    salary=detail.get("compensationTierSummary")
                    or p.get("compensationTierSummary"),
                    description=strip_html(description_html) if description_html else None,
                )
            )
            if on_progress:
                on_progress(len(jobs), len(postings))
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
