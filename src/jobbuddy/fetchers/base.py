"""Abstract base class for ATS fetchers."""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeVar

import httpx

from jobbuddy.models import Job

type ProgressCallback = Callable[[int, int], None]   # (fetched, total)
type FetchedCallback = Callable[[str, str], None]    # (job_id, description)
type RetryCallback = Callable[[int, int, float, str], None]  # (attempt, max_attempts, wait_seconds, reason)
type JobList = list[Job]

T = TypeVar("T")

log = logging.getLogger(__name__)

# Transient errors worth retrying
_RETRYABLE_EXCEPTIONS = (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout)

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
    def list_jobs(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> JobList: ...

    @abstractmethod
    def fetch_job(self, job_id: str) -> Job: ...

    def resolve_name(self) -> str | None:
        """Try to resolve company display name from the ATS. Returns None by default."""
        return None

    def _retry_request(
        self,
        fn: Callable[[], T],
        *,
        on_retry: RetryCallback | None = None,
    ) -> T:
        """Call fn() with retry on 429 and transient network errors.

        Respects Retry-After header on 429 responses. Uses exponential backoff
        for transient errors (ReadTimeout, ConnectError). Non-retryable errors
        are raised immediately.
        """
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return fn()
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (429, 405):
                    raise
                last_exc = e
                if attempt == self.max_retries:
                    raise
                retry_after = e.response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else self.backoff_base * (2 ** attempt)
            except _RETRYABLE_EXCEPTIONS as e:
                last_exc = e
                if attempt == self.max_retries:
                    raise
                wait = self.backoff_base * (2 ** attempt)

            reason = str(last_exc).split("\n", 1)[0]
            log.warning(
                "Retrying (attempt %d/%d, wait %.1fs): %s",
                attempt + 1, self.max_retries, wait, reason,
            )
            if on_retry:
                on_retry(attempt + 1, self.max_retries, wait, str(last_exc))
            time.sleep(wait)

        # Should never reach here, but satisfy type checker
        raise last_exc  # type: ignore[misc]

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
        on_fetched: FetchedCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> dict[str, str | None]:
        """Fetch descriptions for a batch of job IDs with retry/backoff.

        Uses _retry_request() per job with rate limiting. If on_fetched is
        provided, it's called with (job_id, description) after each successful
        fetch so callers can commit incrementally.
        """
        metadata = metadata or {}
        results: dict[str, str | None] = {}
        for job_id in job_ids:
            desc = None
            try:
                desc = self._retry_request(
                    lambda jid=job_id: self.fetch_description(jid, metadata.get(jid)),
                    on_retry=on_retry,
                )
            except Exception as e:
                log.warning("Failed to fetch description for %s: %s", job_id, e)

            results[job_id] = desc
            if on_fetched and desc:
                on_fetched(job_id, desc)

            if self.enrich_delay > 0:
                time.sleep(self.enrich_delay)

        return results
