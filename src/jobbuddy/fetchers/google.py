"""Google Careers fetcher (google.com/about/careers/applications).

Uses Google's internal batchexecute RPC protocol. No authentication required
for public listings. Descriptions are included in listing results (full fetcher).

Company registry fields:
  - ats: "google"
  - board: ignored (single global careers portal)
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

CAREERS_URL = "https://www.google.com/about/careers/applications/jobs/results"
BATCHEXECUTE_PATH = "/about/careers/applications/_/HiringCportalFrontendUi/data/batchexecute"
RPC_ID = "r06xKb"
PAGE_SIZE = 20


def extract_tokens(html: str) -> tuple[str, str]:
    """Extract f.sid and bl (build label) from the careers page HTML.

    Raises ValueError if tokens cannot be found.
    """
    sid_match = re.search(r'"FdrFJe":"(-?\d+)"', html)
    if not sid_match:
        raise ValueError("Could not extract f.sid from Google careers page")

    bl_match = re.search(r'"cfb2h":"(boq_[^"]+)"', html)
    if not bl_match:
        raise ValueError("Could not extract build label from Google careers page")

    return sid_match.group(1), bl_match.group(1)


def build_location(location_entries: list | None) -> str:
    if not location_entries:
        return ""
    names = [entry[0] for entry in location_entries if entry and entry[0]]
    return " | ".join(names)


def build_description(entry: list) -> str | None:
    """Combine summary, description, qualifications, and workplace info."""
    parts = []

    summary = entry[10][1] if entry[10] else None
    if summary:
        parts.append(strip_html(summary))

    desc = entry[3][1] if entry[3] else None
    if desc:
        parts.append(strip_html(desc))

    quals = entry[4][1] if entry[4] else None
    if quals:
        parts.append(strip_html(quals))

    workplace = entry[18][1] if len(entry) > 18 and entry[18] else None
    if workplace:
        parts.append(strip_html(workplace))

    return "\n\n".join(parts) if parts else None


def parse_timestamp(ts: list | None) -> str | None:
    if not ts or not ts[0]:
        return None
    try:
        dt = datetime.fromtimestamp(ts[0], tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return None


def parse_batchexecute_response(text: str) -> list:
    """Parse the batchexecute response format.

    Response starts with )]}'\\n then alternates between byte-count lines
    and JSON array lines. We find the JSON envelope for our RPC.
    """
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(envelope, list)
            and envelope
            and isinstance(envelope[0], list)
            and len(envelope[0]) > 2
            and envelope[0][1] == RPC_ID
        ):
            return json.loads(envelope[0][2])

    raise ValueError("Could not find r06xKb response in batchexecute output")


class GoogleFetcher(ATSFetcher):
    ats_type = "google"

    def __init__(self, board: str, name: str | None = None):
        super().__init__(board, name or "Google")
        self._f_sid: str | None = None
        self._bl: str | None = None
        self.client.headers.update({
            "Origin": "https://www.google.com",
            "Referer": "https://www.google.com/",
            "X-Same-Domain": "1",
        })

    def _ensure_tokens(self, *, on_retry: RetryCallback | None = None) -> None:
        if self._f_sid and self._bl:
            return

        def do_fetch():
            resp = self.client.get(CAREERS_URL)
            resp.raise_for_status()
            return resp.text

        html = self._retry_request(do_fetch, on_retry=on_retry)
        self._f_sid, self._bl = extract_tokens(html)

    def _fetch_page(
        self, page: int, *, include_count: bool = False, on_retry: RetryCallback | None = None,
    ) -> tuple[list[list], int | None]:
        """Fetch a page of results. Returns (job_entries, total_or_none)."""
        self._ensure_tokens(on_retry=on_retry)

        payload: list = ["", None, None, None, "en", None, None, page]
        if include_count:
            payload.extend([None, 1])
        inner = json.dumps([payload])
        outer = [[RPC_ID, inner, None, "3"]]
        f_req = json.dumps([outer])

        params = {
            "rpcids": RPC_ID,
            "source-path": "/about/careers/applications/jobs/results",
            "f.sid": self._f_sid,
            "bl": self._bl,
            "hl": "en",
            "soc-app": "1",
            "soc-platform": "1",
            "soc-device": "1",
            "_reqid": str(100000 + page * 100000),
            "rt": "c",
        }

        def do_fetch():
            resp = self.client.post(
                f"https://www.google.com{BATCHEXECUTE_PATH}",
                params=params,
                content=f"f.req={urllib.parse.quote(f_req)}&",
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            )
            resp.raise_for_status()
            return resp.text

        raw = self._retry_request(do_fetch, on_retry=on_retry)
        data = parse_batchexecute_response(raw)

        entries = data[0] if data[0] else []
        total = data[2] if include_count and len(data) > 2 else None
        return entries, total

    def _entry_to_job(self, entry: list) -> Job:
        job_id = str(entry[0])
        title = entry[1] or ""
        locations = entry[9] if len(entry) > 9 else None

        return Job(
            id=job_id,
            title=title,
            location=build_location(locations),
            url=f"{CAREERS_URL}/{job_id}",
            apply_url=entry[2] or f"{CAREERS_URL}/{job_id}",
            published_at=parse_timestamp(entry[12] if len(entry) > 12 else None),
            department=None,
            description=build_description(entry),
        )

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        # Google's reported total is unreliable (often undercounts), so we
        # paginate until we get an empty page or zero new unique jobs.
        entries, reported_total = self._fetch_page(1, include_count=True, on_retry=on_retry)
        seen: set[str] = set()
        jobs: JobList = []

        for e in entries:
            jid = str(e[0])
            if jid not in seen:
                seen.add(jid)
                jobs.append(self._entry_to_job(e))

        if on_progress:
            on_progress(len(jobs), reported_total or len(jobs))

        page = 2
        while True:
            page_entries, _ = self._fetch_page(page, on_retry=on_retry)
            if not page_entries:
                break
            new_count = 0
            for e in page_entries:
                jid = str(e[0])
                if jid not in seen:
                    seen.add(jid)
                    jobs.append(self._entry_to_job(e))
                    new_count += 1
            if new_count == 0:
                break
            if on_progress:
                on_progress(len(jobs), max(len(jobs), reported_total or 0))
            page += 1

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        for page in range(1, 100):
            entries, _ = self._fetch_page(page)
            if not entries:
                break
            for entry in entries:
                if str(entry[0]) == job_id:
                    return self._entry_to_job(entry)

        raise ValueError(f"Job ID {job_id} not found on Google Careers")
