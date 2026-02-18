"""Paylocity ATS fetcher."""

import json
import re

from jobbuddy.fetchers.base import ATSFetcher, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html


def _base_url(ats_prefix: str = "") -> str:
    return f"https://{ats_prefix}recruiting.paylocity.com"


class PaylocityFetcher(ATSFetcher):
    ats_type = "paylocity"

    def __init__(self, board: str, name: str | None = None, *, company_slug: str = "", ats_prefix: str = ""):
        super().__init__(board, name)
        self.company_slug = company_slug
        self.ats_prefix = ats_prefix

    def _listing_url(self) -> str:
        base = _base_url(self.ats_prefix)
        slug_part = f"/{self.company_slug}" if self.company_slug else ""
        return f"{base}/Recruiting/Jobs/All/{self.board}{slug_part}"

    def _extract_page_data(self, html: str) -> dict:
        """Extract window.pageData JSON from HTML."""
        m = re.search(r"window\.pageData\s*=\s*({.*?});\s*</script>", html, re.DOTALL)
        if not m:
            raise ValueError("Could not find window.pageData in Paylocity page")
        return json.loads(m.group(1))

    def resolve_name(self) -> str | None:
        """Fetch company display name from listing page."""
        try:
            resp = self.client.get(self._listing_url())
            resp.raise_for_status()
            page_data = self._extract_page_data(resp.text)
            return page_data.get("ModuleTitle") or None
        except Exception:
            return None

    def _parse_list_job(self, j: dict) -> Job:
        """Parse a job from the listing page's pageData.Jobs array."""
        job_id = str(j["JobId"])
        location_parts = []
        loc = j.get("JobLocation") or {}
        if loc.get("City"):
            location_parts.append(loc["City"])
        if loc.get("State"):
            location_parts.append(loc["State"])
        location = ", ".join(location_parts) or j.get("LocationName", "")
        if j.get("IsRemote"):
            location = f"{location} (Remote)" if location else "Remote"

        base = _base_url(self.ats_prefix)
        url = f"{base}/Recruiting/Jobs/Details/{job_id}"

        description = j.get("Description", "")
        if description and "<" in description:
            description = strip_html(description)

        return Job(
            id=job_id,
            title=j.get("JobTitle", ""),
            location=location,
            url=url,
            apply_url=url,
            published_at=_parse_date(j.get("PublishedDate")),
            department=j.get("HiringDepartment"),
            description=description.strip() if description else None,
        )

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> list[Job]:
        resp = self.client.get(self._listing_url())
        resp.raise_for_status()
        page_data = self._extract_page_data(resp.text)
        jobs_raw = page_data.get("Jobs", [])
        return [self._parse_list_job(j) for j in jobs_raw]

    def fetch_job(self, job_id: str) -> Job:
        base = _base_url(self.ats_prefix)
        url = f"{base}/Recruiting/Jobs/Details/{job_id}"
        resp = self.client.get(url)
        if resp.status_code == 404:
            raise ValueError(f"Job ID {job_id} not found on Paylocity.")
        resp.raise_for_status()
        return self._parse_detail_page(resp.text, job_id, url)

    def _parse_detail_page(self, html: str, job_id: str, url: str) -> Job:
        """Parse a detail page using Schema.org JSON-LD, with pageData fallback."""
        # Try Schema.org JSON-LD first
        m = re.search(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            ld = json.loads(m.group(1))
            return self._parse_jsonld(ld, job_id, url)

        # Fallback: pageData on detail page
        page_data = self._extract_page_data(html)
        return Job(
            id=job_id,
            title=page_data.get("jobTitle", "Unknown"),
            location="",
            url=url,
            apply_url=url,
        )

    def _parse_jsonld(self, ld: dict, job_id: str, url: str) -> Job:
        """Parse Schema.org JobPosting JSON-LD into a Job."""
        # Location
        location = ""
        job_loc = ld.get("jobLocation", {})
        addr = job_loc.get("address", {}) if isinstance(job_loc, dict) else {}
        if addr:
            parts = []
            if addr.get("addressLocality"):
                parts.append(addr["addressLocality"])
            if addr.get("addressRegion"):
                parts.append(addr["addressRegion"])
            location = ", ".join(parts)

        # Salary
        salary = None
        base_salary = ld.get("baseSalary", {})
        if base_salary and isinstance(base_salary, dict):
            currency = base_salary.get("currency", "USD")
            value_obj = base_salary.get("value", {})
            if isinstance(value_obj, dict):
                val = value_obj.get("value")
                min_val = value_obj.get("minValue")
                max_val = value_obj.get("maxValue")
                unit = value_obj.get("unitText", "")
                unit_label = f"/{unit.lower()}" if unit else ""
                if min_val and max_val:
                    salary = f"{currency} {min_val} - {max_val}{unit_label}"
                elif val:
                    salary = f"{currency} {val}{unit_label}"

        # Description
        description = ld.get("description", "")
        if description and "<" in description:
            description = strip_html(description)

        # Date
        date_posted = ld.get("datePosted", "")
        if date_posted:
            date_posted = date_posted[:10]  # YYYY-MM-DD

        # Org name for potential auto-registration
        org = ld.get("hiringOrganization", {})
        if org and isinstance(org, dict) and not self.name:
            self.name = org.get("name", self.name)

        return Job(
            id=job_id,
            title=ld.get("title", ""),
            location=location,
            url=url,
            apply_url=url,
            published_at=date_posted or None,
            salary=salary,
            description=description.strip() if description else None,
        )


def _parse_date(date_str: str | None) -> str | None:
    """Parse ISO date string to YYYY-MM-DD."""
    if not date_str:
        return None
    # PublishedDate comes as ISO 8601 e.g. "2026-01-15T18:51:33"
    return date_str[:10] if len(date_str) >= 10 else date_str
