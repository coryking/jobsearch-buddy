"""FetchPhase — parallel company fetching via ThreadPoolExecutor."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic
from typing import TYPE_CHECKING

from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company, Job

if TYPE_CHECKING:
    from jobbuddy.store import JobStore
    from jobbuddy.sync import SyncCallbacks, SyncResult

log = logging.getLogger(__name__)


class FetchPhase:
    def __init__(
        self,
        store: JobStore,
        targets: list[Company],
        max_workers: int,
        callbacks: SyncCallbacks,
    ):
        self.store = store
        self.targets = targets
        self.max_workers = max_workers
        self.cb = callbacks

    def run(self) -> tuple[list[SyncResult], list[str]]:
        """Fetch jobs for all target companies in parallel.

        Returns (results, slugs_that_succeeded).
        """
        from jobbuddy.sync import SyncResult

        results: list[SyncResult] = []
        slugs_to_embed: list[str] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_slug = {}
            start_times: dict[str, float] = {}
            for company in self.targets:
                start_times[company.slug] = monotonic()
                future = executor.submit(self._fetch_company, company)
                future_to_slug[future] = company.slug

            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                elapsed = monotonic() - start_times[slug]

                try:
                    result_slug, payload = future.result()
                except Exception as e:
                    sr = SyncResult(slug, error=str(e), elapsed=elapsed)
                    self.store.record_sync_error(slug, str(e))
                    results.append(sr)
                    if self.cb.on_result:
                        self.cb.on_result(sr)
                    continue

                if isinstance(payload, str):
                    sr = SyncResult(slug, error=payload, elapsed=elapsed)
                    self.store.record_sync_error(slug, payload)
                else:
                    self.store.upsert_jobs(slug, payload)
                    slugs_to_embed.append(slug)
                    sr = SyncResult(slug, job_count=len(payload), elapsed=elapsed)

                results.append(sr)
                if self.cb.on_result:
                    self.cb.on_result(sr)

        return results, slugs_to_embed

    def _fetch_company(self, company: Company) -> tuple[str, list[Job] | str]:
        """Worker function: fetch jobs. Returns (slug, jobs) or (slug, error_string)."""
        if self.cb.on_start:
            self.cb.on_start(company.slug)
        try:
            fetcher = get_fetcher(company)

            def _progress(fetched: int, total: int) -> None:
                if self.cb.on_fetch_progress:
                    self.cb.on_fetch_progress(company.slug, fetched, total)

            jobs = fetcher.list_jobs(on_progress=_progress if self.cb.on_fetch_progress else None)
            return (company.slug, jobs)
        except Exception as e:
            return (company.slug, str(e))
