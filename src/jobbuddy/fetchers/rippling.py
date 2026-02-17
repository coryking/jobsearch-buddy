"""Rippling ATS fetcher."""

import httpx

from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Job, strip_html

_BASE = "https://api.rippling.com/platform/api/ats/v1"


class RipplingFetcher(ATSFetcher):
    ats_type = "rippling"

    def _parse_job(self, j: dict) -> Job:
        """Parse a Rippling job response into a Job."""
        desc_parts = []
        desc = j.get("description")
        if isinstance(desc, dict):
            for section in ("role", "company"):
                html = desc.get(section, "")
                if html:
                    desc_parts.append(strip_html(html))
        elif isinstance(desc, str) and desc:
            desc_parts.append(strip_html(desc) if "<" in desc else desc)
        description = "\n\n".join(desc_parts) if desc_parts else None

        location = ""
        work_locs = j.get("workLocations", [])
        if work_locs:
            parts = []
            for loc in work_locs:
                if isinstance(loc, dict):
                    parts.append(loc.get("label", ""))
                elif isinstance(loc, str):
                    parts.append(loc)
            location = " | ".join(p for p in parts if p)
        elif isinstance(j.get("workLocation"), dict):
            location = j["workLocation"].get("label", "")

        dept = j.get("department")
        department = dept.get("label", "") if isinstance(dept, dict) else (dept or "")

        salary = None
        pay = j.get("payRangeDetails")
        if isinstance(pay, dict):
            parts = []
            if pay.get("min"):
                parts.append(str(pay["min"]))
            if pay.get("max"):
                parts.append(str(pay["max"]))
            if parts:
                salary = " - ".join(parts)

        job_id = j.get("uuid", j.get("id", ""))
        url = j.get("url", f"https://ats.rippling.com/{self.board}/jobs/{job_id}")

        return Job(
            id=job_id,
            title=j.get("name", ""),
            location=location,
            url=url,
            apply_url=url,
            published_at=j.get("createdOn", "")[:10] if j.get("createdOn") else None,
            department=department or None,
            team=None,
            salary=salary,
            description=description,
        )

    def list_jobs(self) -> list[Job]:
        url = f"{_BASE}/board/{self.board}/jobs"
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return [self._parse_job(j) for j in resp.json()]

    def fetch_job(self, job_id: str) -> Job:
        url = f"{_BASE}/board/{self.board}/jobs/{job_id}"
        resp = httpx.get(url, timeout=30)
        if resp.status_code == 404:
            raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
        resp.raise_for_status()
        return self._parse_job(resp.json())
