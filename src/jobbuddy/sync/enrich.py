"""EnrichPhase — description enrichment for stub fetchers."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company

if TYPE_CHECKING:
    from jobbuddy.store import JobStore
    from jobbuddy.sync import SyncCallbacks

log = logging.getLogger(__name__)


class EnrichPhase:
    def __init__(
        self,
        store: JobStore,
        slugs: list[str],
        targets: list[Company],
        max_workers: int,
        callbacks: SyncCallbacks,
    ):
        self.store = store
        self.slugs = slugs
        self.slug_to_company = {c.slug: c for c in targets}
        self.max_workers = max_workers
        self.cb = callbacks

    def run(self) -> None:
        """Enrich descriptions for stub fetchers."""
        enrich_total = 0
        # (slug, [job_ids], {job_id: metadata_dict})
        enrich_plan: list[tuple[str, list[str], dict[str, dict]]] = []

        for slug in self.slugs:
            company = self.slug_to_company.get(slug)
            if not company:
                continue
            fetcher = get_fetcher(company)
            if fetcher.descriptions_in_listing:
                continue
            needing = self.store.get_jobs_needing_descriptions(slug)
            if not needing:
                continue
            job_ids = [j["job_id"] for j in needing]
            jobs_meta = {
                j["job_id"]: json.loads(j["ats_metadata"] or "{}")
                for j in needing
            }
            enrich_plan.append((slug, job_ids, jobs_meta))
            enrich_total += len(job_ids)

        if enrich_total == 0:
            return

        if self.cb.on_enrich_start:
            self.cb.on_enrich_start(enrich_total)

        enrich_done = 0
        enrich_lock = threading.Lock()

        def _enrich_company(slug: str, job_ids: list[str], jobs_meta: dict[str, dict]) -> None:
            nonlocal enrich_done
            company = self.slug_to_company[slug]
            try:
                fetcher = get_fetcher(company)

                def _on_fetched(job_id: str, desc: str, _slug: str = slug) -> None:
                    nonlocal enrich_done
                    with enrich_lock:
                        self.store.update_descriptions(_slug, {job_id: desc})
                        enrich_done += 1
                        if self.cb.on_enrich_progress:
                            self.cb.on_enrich_progress(enrich_done, enrich_total)

                fetcher.fetch_descriptions(
                    job_ids,
                    metadata=jobs_meta,
                    on_fetched=_on_fetched,
                )
            except Exception as e:
                log.warning("Description enrichment failed for %s: %s", slug, e)
                with enrich_lock:
                    enrich_done += len(job_ids)
                    if self.cb.on_enrich_progress:
                        self.cb.on_enrich_progress(enrich_done, enrich_total)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(_enrich_company, slug, job_ids, jobs_meta)
                for slug, job_ids, jobs_meta in enrich_plan
            ]
            for future in as_completed(futures):
                future.result()

        if self.cb.on_enrich_done:
            self.cb.on_enrich_done()
