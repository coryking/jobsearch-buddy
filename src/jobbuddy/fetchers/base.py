"""Abstract base class for ATS fetchers."""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import httpx

from jobbuddy.models import Job

log = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class ATSFetcher(ABC):
    ats_type: str  # set by each subclass

    # Whether list_jobs() returns descriptions. Override to False in fetchers
    # that only return stubs (Workday, Eightfold, Oracle HCM) so the sync
    # pipeline knows to run a post-sync enrichment phase.
    descriptions_in_listing: bool = True

    # Rate limiting config for enrichment — override in subclasses
    enrich_delay: float = 0.0
    max_retries: int = 3
    backoff_base: float = 2.0

    def __init__(self, board: str, name: str | None = None):
        self.board = board
        self.name = name or board.replace("-", " ").replace("_", " ").title()
        self.client = httpx.Client(
            headers=_DEFAULT_HEADERS.copy(),
            timeout=30,
            follow_redirects=True,
        )

    @abstractmethod
    def list_jobs(self) -> list[Job]: ...

    @abstractmethod
    def fetch_job(self, job_id: str) -> Job: ...

    def resolve_name(self) -> str | None:
        """Try to resolve company display name from the ATS. Returns None by default."""
        return None

    def fetch_description(self, job_id: str, metadata: dict | None = None) -> str | None:
        """Fetch description for a single job. Override for optimized per-job fetching.

        Default calls fetch_job() and returns its description. Subclasses can
        override to skip building a full Job object (e.g. Eightfold calls the
        detail API directly for just the description).
        """
        job = self.fetch_job(job_id)
        return job.description

    def fetch_descriptions(
        self,
        job_ids: list[str],
        *,
        metadata: dict[str, dict] | None = None,
        on_fetched: Callable[[str, str], None] | None = None,
    ) -> dict[str, str | None]:
        """Fetch descriptions for a batch of job IDs with retry/backoff.

        Uses fetch_description() per job with rate limiting and 429 retry logic.
        If on_fetched is provided, it's called with (job_id, description) after
        each successful fetch so callers can commit incrementally.
        """
        metadata = metadata or {}
        results: dict[str, str | None] = {}
        for job_id in job_ids:
            desc = None
            for attempt in range(self.max_retries + 1):
                try:
                    desc = self.fetch_description(job_id, metadata.get(job_id))
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        if attempt == self.max_retries:
                            log.warning("Max retries on 429 for %s", job_id)
                            break
                        retry_after = e.response.headers.get("Retry-After")
                        if retry_after:
                            wait = float(retry_after)
                        else:
                            wait = self.backoff_base * (2 ** attempt)
                        log.info("429 for job %s, backing off %.1fs (attempt %d/%d)",
                                 job_id, wait, attempt + 1, self.max_retries)
                        time.sleep(wait)
                    else:
                        log.warning("Failed to fetch description for %s: %s", job_id, e)
                        break
                except Exception as e:
                    log.warning("Failed to fetch description for %s: %s", job_id, e)
                    break

            results[job_id] = desc
            if on_fetched and desc:
                on_fetched(job_id, desc)

            if self.enrich_delay > 0:
                time.sleep(self.enrich_delay)

        return results
