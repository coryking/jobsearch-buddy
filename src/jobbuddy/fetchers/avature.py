"""Avature ATS fetcher.

Avature career sites are server-rendered HTML at {company}.avature.net.
Job listings are at /{section}/SearchJobs with offset-based pagination.
Job details are at /{section}/JobDetail/{slug}/{id}.

Company registry fields:
  - ats: "avature"
  - board: subdomain (e.g. "bloomberg" for bloomberg.avature.net)
  - av_section: portal section path (e.g. "careers", "main", "CollegeRecruiting")
  - av_locale: optional locale prefix (e.g. "en_US"); omit if not needed
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobbuddy.fetchers.base import ATSFetcher, JobList, ProgressCallback, RetryCallback
from jobbuddy.models import Job, strip_html

log = logging.getLogger(__name__)

_RECORDS_PER_PAGE = 20


class AvatureFetcher(ATSFetcher):
    ats_type = "avature"
    descriptions_in_listing = False
    enrich_delay = 0.2

    def __init__(
        self,
        board: str,
        name: str | None = None,
        *,
        av_section: str = "careers",
        av_locale: str = "",
    ):
        super().__init__(board, name)
        self.av_section = av_section
        self.av_locale = av_locale

    def _base_url(self) -> str:
        return f"https://{self.board}.avature.net"

    def _section_path(self) -> str:
        if self.av_locale:
            return f"/{self.av_locale}/{self.av_section}"
        return f"/{self.av_section}"

    def _search_url(self, offset: int) -> str:
        return (
            f"{self._base_url()}{self._section_path()}/SearchJobs"
            f"?jobRecordsPerPage={_RECORDS_PER_PAGE}&jobOffset={offset}"
        )

    def _parse_search_html(self, html: str) -> tuple[list[Job], int]:
        """Parse Avature search results HTML. Returns (jobs, total)."""
        # Total: "X of Y results" or "Y results"
        total_match = re.search(r"of\s+(\d+)\s+results", html)
        if not total_match:
            total_match = re.search(r"(\d+)\s+results", html)
        total = int(total_match.group(1)) if total_match else 0

        jobs: list[Job] = []

        # Job listings are in <article class="article article--result"> blocks.
        # Each has an <a class="link" href="...JobDetail/{slug}/{id}"> with the title,
        # and a <span class="list-item-location"> with the location.
        articles = re.findall(
            r'<article class="article article--result"[^>]*>(.*?)</article>',
            html,
            re.DOTALL,
        )

        link_pattern = re.compile(
            r'<a\s+class="link"\s+href="([^"]*JobDetail/[^"]+/(\d+))"[^>]*>\s*(.*?)\s*</a>',
            re.DOTALL,
        )
        loc_pattern = re.compile(
            r'<span\s+class="list-item-location">\s*(.*?)\s*</span>',
            re.DOTALL,
        )

        for article in articles:
            link_match = link_pattern.search(article)
            if not link_match:
                continue

            raw_url = link_match.group(1)
            job_id = link_match.group(2)
            title = re.sub(r"\s+", " ", link_match.group(3)).strip()

            # Normalize URL — may be absolute or relative
            if raw_url.startswith("/"):
                url = f"{self._base_url()}{raw_url}"
            elif raw_url.startswith("http"):
                url = raw_url
            else:
                url = f"{self._base_url()}/{raw_url}"

            loc_match = loc_pattern.search(article)
            location = loc_match.group(1).strip() if loc_match else ""

            jobs.append(
                Job(
                    id=job_id,
                    title=title,
                    location=location,
                    url=url,
                    apply_url=url,
                    ats_metadata={"url": url},
                )
            )

        return jobs, total

    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList:
        # Fetch page 0 to learn total
        resp = self._retry_request(
            lambda: self.client.get(self._search_url(0)),
            on_retry=on_retry,
        )
        resp.raise_for_status()
        jobs, total = self._parse_search_html(resp.text)
        if on_progress:
            on_progress(len(jobs), total)

        # The server may ignore our jobRecordsPerPage and use its own page size.
        # Use the actual count from page 1 as the step size.
        page_size = len(jobs) or _RECORDS_PER_PAGE
        if total <= page_size:
            return jobs

        remaining_offsets = list(range(page_size, total, page_size))
        lock = threading.Lock()

        def _fetch_page(offset: int) -> list[Job]:
            page_resp = self._retry_request(
                lambda o=offset: self.client.get(self._search_url(o)),
                on_retry=on_retry,
            )
            page_resp.raise_for_status()
            page_jobs, _ = self._parse_search_html(page_resp.text)
            return page_jobs

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_page, o): o for o in remaining_offsets}
            for future in as_completed(futures):
                page_jobs = future.result()
                with lock:
                    jobs.extend(page_jobs)
                    current = len(jobs)
                if on_progress:
                    on_progress(current, total)

        return jobs

    def fetch_job(self, job_id: str) -> Job:
        jobs = self.list_jobs()
        for j in jobs:
            if j.id == job_id:
                desc = self.fetch_description(job_id, metadata={"url": j.url})
                if desc:
                    j = j.model_copy(update={"description": desc})
                return j
        raise ValueError(f"Job ID {job_id} not found on {self.name}.")

    def _extract_description_from_html(self, html: str) -> str | None:
        """Extract job description from an Avature detail page.

        The description lives in the last <article class="article article--details">
        block, which contains a "Description & Requirements" (or similar) section
        with rich HTML inside article__content__view__field__value divs.
        """
        articles = re.findall(
            r'<article class="article article--details[^"]*"[^>]*>(.*?)</article>',
            html,
            re.DOTALL,
        )
        if not articles:
            return None

        # The last (or largest) article is typically the description
        desc_article = max(articles, key=len)

        # Extract all field values from the description article
        values = re.findall(
            r'<div class="article__content__view__field__value">\s*(.*?)\s*</div>',
            desc_article,
            re.DOTALL,
        )
        if not values:
            return None

        # The rich-text field value is the one with HTML content
        # Pick the longest value (the actual description, not labels)
        desc_html = max(values, key=len)
        if len(desc_html) < 50:
            # Too short to be a real description, try concatenating
            desc_html = "\n".join(values)

        return strip_html(desc_html) if desc_html else None

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        url = (metadata or {}).get("url")
        if not url:
            jobs = self.list_jobs()
            for j in jobs:
                if j.id == job_id:
                    url = j.url
                    break

        if not url:
            log.warning("No URL found for job %s, cannot fetch description", job_id)
            return None

        resp = self._retry_request(lambda: self.client.get(url))
        resp.raise_for_status()
        return self._extract_description_from_html(resp.text)
