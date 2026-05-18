"""Rippling ATS fetcher.

The Rippling list endpoint (/board/{board}/jobs) returns only
{department, name, url, uuid, workLocation} — no description, no createdOn,
no payRangeDetails. Those fields live only on the detail endpoint
(/board/{board}/jobs/{uuid}), so this is a stub fetcher: list_jobs() emits
stubs, and the enrich phase fetches each detail page individually for
description + published_at + salary.
"""

import json
import logging
import re
from datetime import date
from typing import Any

from jobbuddy.fetchers.base import (
    ApplicationFormNotSupported,
    ATSFetcher,
    ProgressCallback,
    RetryCallback,
)
from jobbuddy.models import Job, strip_html

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

log = logging.getLogger(__name__)

_BASE = "https://api.rippling.com/platform/api/ats/v1"


def _parse_created_on(raw: str | None) -> date | None:
    """Parse Rippling's createdOn ISO timestamp into a date.

    Format is `YYYY-MM-DDTHH:MM:SS.fff[+-]HH:MM`. We only want the date part.
    Pydantic would coerce the truncated ISO string, but doing it here keeps
    the field type honest and surfaces malformed payloads at fetch time
    instead of at Job construction.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        log.warning("Rippling createdOn unparseable: %r", raw)
        return None


class RipplingFetcher(ATSFetcher):
    ats_type = "rippling"
    descriptions_in_listing = False
    enrichment_fills = ("description", "published_at")

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
            published_at=_parse_created_on(j.get("createdOn")),
            department=department or None,
            team=None,
            salary=salary,
            description=description,
        )

    def list_jobs(self, *, on_progress: ProgressCallback | None = None, on_retry: RetryCallback | None = None) -> list[Job]:
        url = f"{_BASE}/board/{self.board}/jobs"
        resp = self.client.get(url)
        resp.raise_for_status()
        return [self._parse_job(j) for j in resp.json()]

    def fetch_job(self, job_id: str) -> Job:
        url = f"{_BASE}/board/{self.board}/jobs/{job_id}"
        resp = self.client.get(url)
        if resp.status_code == 404:
            raise ValueError(f"Job ID {job_id} not found on {self.board} board.")
        resp.raise_for_status()
        return self._parse_job(resp.json())

    def fetch_application_form(self, board: str, job_id: str) -> dict[str, Any]:
        """Return Rippling's `activeJobApplication` subtree from listing HTML.

        Rippling embeds form data as a Next.js __NEXT_DATA__ JSON blob inside
        the candidate-facing listing page at ats.rippling.com. The path is
        `props.pageProps.apiData.jobPost.activeJobApplication`. Returning the
        whole subtree (customQuestions, additionalQuestions,
        eeocQuestionnaireEnabled, etc.) keeps every signal the LLM might want.
        """
        url = f"https://ats.rippling.com/{board}/jobs/{job_id}"
        resp = self.client.get(url)
        resp.raise_for_status()
        m = _NEXT_DATA_RE.search(resp.text)
        if not m:
            raise ApplicationFormNotSupported(
                f"Rippling listing {board}/{job_id} has no __NEXT_DATA__ blob"
            )
        try:
            next_data = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise ApplicationFormNotSupported(
                f"Rippling __NEXT_DATA__ unparseable for {board}/{job_id}: {e}"
            ) from e
        try:
            active = (
                next_data["props"]["pageProps"]["apiData"]["jobPost"][
                    "activeJobApplication"
                ]
            )
        except (KeyError, TypeError):
            active = None
        if not active:
            raise ApplicationFormNotSupported(
                f"Rippling listing {board}/{job_id} has no activeJobApplication"
            )
        return active

    def fetch_enrichment(self, job_id: str, metadata: dict | None = None) -> dict[str, Any]:
        """Fetch description, published_at, and salary from a single detail
        endpoint request. Avoids the wasted work of pulling the full job and
        throwing away everything but the description."""
        job = self.fetch_job(job_id)
        payload: dict[str, Any] = {}
        if job.description is not None:
            payload["description"] = job.description
        if job.published_at is not None:
            payload["published_at"] = job.published_at
        if job.salary is not None:
            payload["salary"] = job.salary
        return payload
