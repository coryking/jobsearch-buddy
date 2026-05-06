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
        # Per-company outcome counters surface what enrich actually produced —
        # raw "done" hides the silent-empty case where the parser yields {}.
        self._outcomes: dict[str, dict[str, int]] = {}

    def _bump(self, slug: str, outcome: str) -> None:
        self._outcomes.setdefault(slug, {"filled": 0, "empty": 0, "gone": 0, "error": 0})[outcome] += 1

    def _get_fetcher(self, slug: str) -> ATSFetcher:
        """Get or create a fetcher for a company slug, caching to avoid FD leaks."""
        if slug not in self._fetcher_cache:
            company = self.slug_to_company[slug]
            self._fetcher_cache[slug] = get_fetcher(company)
        return self._fetcher_cache[slug]

    def count_remaining(self) -> int:
        """Build enrichment plan and return total jobs needing enrichment.

        Each fetcher declares which columns its detail-page request can
        populate via `enrichment_fills`; we ask the store for jobs where
        ANY of those columns is NULL, so a TalentBrew job that already has
        a description but is missing published_at gets re-fetched.
        """
        total = 0
        for slug in self.slugs:
            company = self.slug_to_company.get(slug)
            if not company or not company.ats:
                continue
            if has_descriptions_in_listing(company.ats):
                continue
            fetcher = self._get_fetcher(slug)
            cols = fetcher.enrichment_fills
            needing = self._run_reader_query(
                lambda r, s=slug, c=cols: r.get_jobs_needing_enrichment(s, c)
            )
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

        def _on_fetched(job_id: str, payload: dict) -> None:
            self.submit_write(
                lambda store, s=slug, jid=job_id, p=payload: store.update_enrichment(s, {jid: p})
            )
            self._bump(slug, "filled")
            self.display.advance(detail=slug)

        def _on_gone(job_id: str) -> None:
            self.submit_write(
                lambda store, s=slug, jid=job_id: store.mark_listing_removed(s, jid)
            )
            self._bump(slug, "gone")
            self.display.advance(detail=slug)

        def _on_empty(job_id: str) -> None:
            self._bump(slug, "empty")
            self.display.advance(detail=slug)

        def _on_error(job_id: str) -> None:
            self._bump(slug, "error")
            self.display.advance(detail=slug)

        try:
            fetcher.fetch_enrichments(
                job_ids,
                metadata=jobs_meta,
                on_fetched=_on_fetched,
                on_gone=_on_gone,
                on_empty=_on_empty,
                on_error=_on_error,
            )
        except Exception as e:
            log.warning("Enrichment failed for %s: %s", slug, e)
            raise

    def on_phase_end(self) -> None:
        """Log per-company outcome breakdown so silent-empty failures
        (request succeeded, parser found nothing, row stays NULL) are
        visible without --verbose."""
        if not self._outcomes:
            return
        totals = {"filled": 0, "empty": 0, "gone": 0, "error": 0}
        for slug, counts in sorted(self._outcomes.items()):
            log.info(
                "enrich summary slug=%s filled=%d empty=%d gone=%d error=%d",
                slug, counts["filled"], counts["empty"], counts["gone"], counts["error"],
            )
            for k, v in counts.items():
                totals[k] += v
        log.info(
            "enrich summary total filled=%d empty=%d gone=%d error=%d",
            totals["filled"], totals["empty"], totals["gone"], totals["error"],
        )
