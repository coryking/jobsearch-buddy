"""ResearchPhase -- Azure Responses API + web_search to fill company bios.

Polls companies.long_bio IS NULL, runs the researcher per company in a
thread pool, writes results via WriteQueue. Same WorkerPhase machinery as
EnrichPhase / StripPhase."""

from __future__ import annotations

import logging

from jobbuddy.research import ResearchError, research_company
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import ResearchWorkItem

log = logging.getLogger(__name__)


class ResearchPhase(WorkerPhase["ResearchWorkItem"]):
    """Fill `companies.long_bio` and `companies.short_bio` for unresearched rows."""

    def __init__(self, conninfo: str, *, display: PhaseState, max_workers: int = 8):
        super().__init__(conninfo, max_workers=max_workers, display=display)

    def item_key(self, item: ResearchWorkItem) -> str:
        return item["slug"]

    def item_label(self, item: ResearchWorkItem) -> str:
        return f"{item['slug']} ({item['name']})"

    def count_remaining(self) -> int:
        return self._get_reader().count_companies_needing_bio()

    def poll_work(self, batch_size: int) -> list[ResearchWorkItem]:
        return self._get_reader().get_companies_needing_bio(limit=batch_size)

    def process_item(self, item: ResearchWorkItem) -> None:
        slug = item["slug"]
        name = item["name"]
        try:
            bio = research_company(name)
        except ResearchError as e:
            log.warning("Research failed for %s: %s", slug, e)
            raise

        self.submit_write(
            lambda store, sl=slug, b=bio: store.update_company_bio(
                sl, short_bio=b.short_bio, long_bio=b.long_bio, model=b.model,
            )
        )
        self.display.add_to_info_counter(bio.web_search_count, label=" search")
        self.display.advance(detail=f"{slug} ({bio.web_search_count} searches)")
