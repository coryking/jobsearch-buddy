"""Abstract base class for ATS fetchers."""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from jobbuddy.models import Job

type ProgressCallback = Callable[[int, int], None]   # (fetched, total)
type EnrichmentCallback = Callable[[str, dict[str, Any]], None]  # (job_id, payload)
type GoneCallback = Callable[[str], None]  # (job_id) — listing 404'd, no longer active
type EmptyCallback = Callable[[str], None]  # (job_id) — fetch succeeded but no fields extractable
type ErrorCallback = Callable[[str], None]  # (job_id) — non-404 failure
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

    # Job columns this fetcher can populate during the enrich phase. The
    # enrich phase polls jobs where any of these columns are NULL, so a
    # fetcher should only declare a column it can plausibly fill from a
    # detail-page request. Default: just description (the original
    # enrichment contract). Fetchers that also extract a posted date,
    # salary, etc. from detail pages add those here.
    enrichment_fills: tuple[str, ...] = ("description",)

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
                if e.response.status_code != 429:
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

    def fetch_enrichment(self, job_id: str, metadata: dict | None = None) -> dict[str, Any]:
        """Fetch all enrichable fields for a single job from a detail-page request.

        Returns a dict whose keys are a subset of self.enrichment_fills. Default
        impl calls fetch_description() and wraps as {"description": str}; fetchers
        that can extract more from a single detail-page request (TalentBrew's
        datePosted, Rippling's createdOn + payRangeDetails) override this so the
        enrich phase captures everything from one HTTP call.

        An empty dict means "nothing extractable" — the row stays as-is and
        the upsert's scrape-date fallback handles missing published_at.
        """
        desc = self.fetch_description(job_id, metadata)
        return {"description": desc} if desc is not None else {}

    def fetch_enrichments(
        self,
        job_ids: list[str],
        *,
        metadata: dict[str, dict] | None = None,
        on_fetched: EnrichmentCallback | None = None,
        on_gone: GoneCallback | None = None,
        on_empty: EmptyCallback | None = None,
        on_error: ErrorCallback | None = None,
        on_retry: RetryCallback | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch enrichment payloads for a batch of job IDs with retry/backoff.

        Every job_id produces exactly one outcome callback so callers can
        track real progress and not just successful writes:

        - filled — payload has at least one field → on_fetched(job_id, payload)
        - empty  — request succeeded but parser found nothing → on_empty(job_id)
        - gone   — 404 from the ATS detail page → on_gone(job_id)
        - error  — any other failure → on_error(job_id)

        Each outcome also emits a DEBUG line keyed by job_id and outcome, so
        `--verbose` gives a per-job trace. 404 keeps its INFO line because
        "listing pulled at the source" is operationally interesting even at
        normal verbosity.
        """
        metadata = metadata or {}
        results: dict[str, dict[str, Any]] = {}
        for job_id in job_ids:
            payload: dict[str, Any] = {}
            try:
                payload = self._retry_request(
                    lambda jid=job_id: self.fetch_enrichment(jid, metadata.get(jid)),
                    on_retry=on_retry,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    log.debug("enrichment outcome job=%s outcome=gone", job_id)
                    log.info("Listing no longer active for %s (404)", job_id)
                    if on_gone:
                        on_gone(job_id)
                else:
                    log.debug("enrichment outcome job=%s outcome=error reason=%s", job_id, e)
                    log.warning("Failed to fetch enrichment for %s: %s", job_id, e)
                    if on_error:
                        on_error(job_id)
            except Exception as e:
                log.debug("enrichment outcome job=%s outcome=error reason=%s", job_id, e)
                log.warning("Failed to fetch enrichment for %s: %s", job_id, e)
                if on_error:
                    on_error(job_id)
            else:
                if payload:
                    log.debug("enrichment outcome job=%s outcome=filled cols=%s",
                              job_id, sorted(payload.keys()))
                    if on_fetched:
                        on_fetched(job_id, payload)
                else:
                    log.debug("enrichment outcome job=%s outcome=empty", job_id)
                    if on_empty:
                        on_empty(job_id)

            results[job_id] = payload

            if self.enrich_delay > 0:
                time.sleep(self.enrich_delay)

        return results

