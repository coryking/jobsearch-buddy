"""Lever ATS fetcher."""

import re
from datetime import datetime, timezone

import httpx

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html


class LeverFetcher(ATSFetcher):
    ats_type = "lever"

    def resolve_name(self) -> str | None:
        """Fetch company display name from Lever page title."""
        try:
            resp = self.client.get(f"https://jobs.lever.co/{self.board}")
            resp.raise_for_status()
            m = re.search(r"<title>(.*?)</title>", resp.text)
            if m:
                name = m.group(1)
                for suffix in (" jobs", " Jobs", " - Job Board"):
                    if name.endswith(suffix):
                        name = name[: -len(suffix)]
                return name.strip() or None
        except httpx.HTTPError:
            pass
        return None

    def _parse_job(self, j: dict) -> Job:
        """Parse a single Lever job posting dict into a Job."""
        salary = None
        comp = j.get("salaryRange")
        if comp:
            parts = []
            if comp.get("min"):
                parts.append(str(comp["min"]))
            if comp.get("max"):
                parts.append(str(comp["max"]))
            if parts:
                currency = comp.get("currency", "USD")
                salary = f"{currency} {' - '.join(parts)}"

        categories = j.get("categories", {})
        location = j.get("workplaceType", "")
        if categories.get("location"):
            location = categories["location"]

        description_html = j.get("descriptionPlain") or j.get("description", "")
        if "<" in description_html:
            description = strip_html(description_html)
        else:
            description = description_html

        lists = j.get("lists", [])
        for lst in lists:
            section_text = lst.get("text", "")
            section_content = lst.get("content", "")
            if section_text:
                description += f"\n\n## {section_text}\n"
            if section_content:
                if "<" in section_content:
                    description += strip_html(section_content)
                else:
                    description += section_content

        created_ms = j.get("createdAt")
        published_at = None
        if created_ms:
            published_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        return Job(
            id=j["id"],
            title=j.get("text", ""),
            location=location,
            url=j.get("hostedUrl", ""),
            apply_url=j.get("applyUrl", ""),
            published_at=published_at,
            department=categories.get("department"),
            team=categories.get("team"),
            salary=salary,
            description=description.strip() if description else None,
        )

    def list_jobs(self) -> list[Job]:
        url = f"https://api.lever.co/v0/postings/{self.board}?mode=json"
        resp = self.client.get(url)
        resp.raise_for_status()
        return [self._parse_job(j) for j in resp.json()]

    def fetch_job(self, job_id: str) -> Job:
        url = f"https://api.lever.co/v0/postings/{self.board}/{job_id}"
        resp = self.client.get(url)
        if resp.status_code == 404:
            raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
        resp.raise_for_status()
        return self._parse_job(resp.json())
