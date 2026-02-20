"""EnrichPhase -- description enrichment for stub fetchers.

Stub fetchers (Workday, etc.) return job listings without descriptions.
This phase fetches full descriptions from ATS URLs, one company at a time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState

log = logging.getLogger(__name__)


class EnrichPhase(WorkerPhase):
    """Fetch full descriptions for jobs from stub fetchers.

    Work unit = (company_slug, [job_dicts], {job_id: metadata_dict}).
    Sequential per-company (rate limit mitigation), but the phase itself
    runs companies through the thread pool.
    """

    def __init__(self, db_path: str | Path, *, slugs: list[str],
                 targets: list[Company], display: PhaseState,
                 max_workers: int = 5):
        super().__init__(db_path, max_workers=max_workers, display=display)
        self.slugs = slugs
        self.slug_to_company = {c.slug: c for c in targets}
        self._enrich_plan: list[tuple[str, list[str], dict[str, dict]]] = []

    def count_remaining(self) -> int:
        """Build enrichment plan and return total jobs needing descriptions."""
        total = 0
        for slug in self.slugs:
            company = self.slug_to_company.get(slug)
            if not company:
                continue
            fetcher = get_fetcher(company)
            if fetcher.descriptions_in_listing:
                continue
            needing = self._reader.get_jobs_needing_descriptions(slug)
            if not needing:
                continue
            job_ids = [j["job_id"] for j in needing]
            jobs_meta = {
                j["job_id"]: json.loads(j["ats_metadata"] or "{}")
                for j in needing
            }
            self._enrich_plan.append((slug, job_ids, jobs_meta))
            total += len(job_ids)
        return total

    def poll_work(self, batch_size: int) -> list[tuple[str, list[str], dict[str, dict]]]:
        """Return one company at a time from the pre-built plan."""
        if not self._enrich_plan:
            return []
        return [self._enrich_plan.pop(0)]

    def process_item(self, item: tuple[str, list[str], dict[str, dict]]) -> None:
        slug, job_ids, jobs_meta = item
        company = self.slug_to_company[slug]
        fetcher = get_fetcher(company)
        store = self._get_thread_store()

        def _on_fetched(job_id: str, desc: str) -> None:
            store.update_descriptions(slug, {job_id: desc})
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
