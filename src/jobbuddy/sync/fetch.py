"""FetchPhase — parallel company fetching via ThreadPoolExecutor."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic

import humanize

from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company, Job
from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState
from jobbuddy.sync.types import SyncResult

log = logging.getLogger(__name__)


class FetchPhase:
    def __init__(
        self,
        store: JobStore,
        targets: list[Company],
        max_workers: int,
        display: PhaseState,
    ):
        self.store = store
        self.targets = targets
        self.max_workers = max_workers
        self.display = display

    def run(self) -> tuple[list[SyncResult], list[str]]:
        """Fetch jobs for all target companies in parallel.

        Returns (results, slugs_that_succeeded).
        """
        results: list[SyncResult] = []
        slugs_to_embed: list[str] = []

        self.display.start(len(self.targets))
        self.display.max_workers = self.max_workers
        log.info("Fetch phase starting (%d companies)", len(self.targets))

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
                    self.display.record_error()
                    self.display.advance()
                    self.display.remove_active_detail(slug)
                    continue

                if isinstance(payload, str):
                    sr = SyncResult(slug, error=payload, elapsed=elapsed)
                    self.store.record_sync_error(slug, payload)
                    self.display.record_error()
                else:
                    self.store.upsert_jobs(slug, payload)
                    slugs_to_embed.append(slug)
                    sr = SyncResult(slug, job_count=len(payload), elapsed=elapsed)
                    if sr.job_count:
                        self.display.add_to_info_counter(sr.job_count, " jobs")

                results.append(sr)
                self.display.advance()
                self.display.remove_active_detail(slug)

        self.display.finish()
        ok = sum(1 for r in results if r.ok)
        errs = sum(1 for r in results if not r.ok)
        total_jobs = sum(r.job_count or 0 for r in results)
        log.info("Fetch phase done (%d ok, %d errors, %d jobs)", ok, errs, total_jobs)
        return results, slugs_to_embed

    def _fetch_company(self, company: Company) -> tuple[str, list[Job] | str]:
        """Worker function: fetch jobs. Returns (slug, jobs) or (slug, error_string)."""
        self.display.set_active_detail(company.slug, company.slug)
        try:
            fetcher = get_fetcher(company)

            def _progress(fetched: int, total: int) -> None:
                self.display.set_active_detail(
                    company.slug,
                    f"{company.slug}: {humanize.intcomma(fetched)}/{humanize.intcomma(total)} jobs",
                )

            jobs = fetcher.list_jobs(on_progress=_progress)
            return (company.slug, jobs)
        except Exception as e:
            return (company.slug, str(e))
