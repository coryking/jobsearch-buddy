"""EnrichPhase -- description enrichment for stub fetchers.

Stub fetchers (Workday, etc.) return job listings without descriptions.
This phase fetches full descriptions from ATS URLs, one company at a time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from jobbuddy.fetchers import get_fetcher, has_descriptions_in_listing
from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.models import Company
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import EnrichWorkItem

log = logging.getLogger(__name__)


class EnrichPhase(WorkerPhase["EnrichWorkItem"]):
    """Fetch full descriptions for jobs from stub fetchers.

    Work unit = EnrichWorkItem (slug, job_ids, jobs_meta).
    Sequential per-company (rate limit mitigation), but the phase itself
    runs companies through the thread pool.
    """

    def __init__(self, conninfo: str, *, slugs: list[str],
                 targets: list[Company], display: PhaseState,
                 max_workers: int = 5,
                 conninfo_factory: Callable[[], str] | None = None):
        super().__init__(
            conninfo, max_workers=max_workers, display=display,
            conninfo_factory=conninfo_factory,
        )
        self.slugs = slugs
        self.slug_to_company = {c.slug: c for c in targets}
        self._enrich_plan: list[EnrichWorkItem] = []
        self._fetcher_cache: dict[str, ATSFetcher] = {}

    def _get_fetcher(self, slug: str) -> ATSFetcher:
        """Get or create a fetcher for a company slug, caching to avoid FD leaks."""
        if slug not in self._fetcher_cache:
            company = self.slug_to_company[slug]
            self._fetcher_cache[slug] = get_fetcher(company)
        return self._fetcher_cache[slug]

    def count_remaining(self) -> int:
        """Build enrichment plan and return total jobs needing descriptions."""
        total = 0
        for slug in self.slugs:
            company = self.slug_to_company.get(slug)
            if not company or not company.ats:
                continue
            if has_descriptions_in_listing(company.ats):
                continue
            needing = self._get_reader().get_jobs_needing_descriptions(slug)
            if not needing:
                continue
            job_ids = [j["job_id"] for j in needing]
            jobs_meta = {
                j["job_id"]: j["ats_metadata"] or {}
                for j in needing
            }
            self._enrich_plan.append({"slug": slug, "job_ids": job_ids, "jobs_meta": jobs_meta})
            total += len(job_ids)
        return total

    def item_key(self, item: EnrichWorkItem) -> str:
        return item["slug"]

    def item_label(self, item: EnrichWorkItem) -> str:
        return f"{item['slug']} ({len(item['job_ids'])} jobs)"

    def poll_work(self, batch_size: int) -> list[EnrichWorkItem]:
        """Return one company at a time from the pre-built plan."""
        if not self._enrich_plan:
            return []
        return [self._enrich_plan.pop(0)]

    def process_item(self, item: EnrichWorkItem) -> None:
        slug = item["slug"]
        job_ids = item["job_ids"]
        jobs_meta = item["jobs_meta"]
        fetcher = self._get_fetcher(slug)

        def _on_fetched(job_id: str, desc: str) -> None:
            self.submit_write(lambda store, s=slug, jid=job_id, d=desc: store.update_descriptions(s, {jid: d}))
            self.display.advance(detail=slug)

        try:
            fetcher.fetch_descriptions(
                job_ids,
                metadata=jobs_meta,
                on_fetched=_on_fetched,
            )
        except Exception as e:
            log.warning("Description enrichment failed for %s: %s", slug, e)
            # Count remaining jobs in this company as done (with errors implied)
            remaining = len(job_ids) - self.display.done
            # Just log -- the base class _safe_process will record the error
            raise
