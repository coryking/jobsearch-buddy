"""ResearchPhase -- Azure Responses API + web_search to fill company bios,
then immediately embed long_bio for find_companies vector search.

Bio + embedding are paired: every newly-written long_bio gets embedded in
the same `process_item` so a research run leaves the company in a fully
queryable state. There is no standalone embed phase — the recovery path
for a bio whose embedding wasn't written (e.g. transient API failure) is
to re-run `jsb research-companies --company <slug> --force`.

Polls companies.long_bio IS NULL, runs the researcher per company in a
thread pool, writes results via WriteQueue. Same WorkerPhase machinery as
EnrichPhase / DistillPhase. Permanent failures (content_filter, schema
mismatches) are recorded as errors and skipped — no retry. Transient
failures fall through to WorkerPhase's retry loop. Embedding failures are
swallowed (logged) so a flaky embedding API doesn't lose a successful
research result; the backfill phase will catch the missing embedding."""

from __future__ import annotations

import logging
from collections.abc import Callable

from openai import OpenAI

from jobbuddy.embeddings import embed_text
from jobbuddy.research import (
    PermanentResearchError,
    build_research_client,
    research_company,
)
from jobbuddy.settings import get_settings
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import ResearchWorkItem

log = logging.getLogger(__name__)


class ResearchPhase(WorkerPhase["ResearchWorkItem"]):
    """Fill `companies.long_bio` and `companies.short_bio` for unresearched rows."""

    def __init__(
        self, conninfo: str, *,
        display: PhaseState,
        max_workers: int | None = None,
        slugs: list[str] | None = None,
        conninfo_factory: Callable[[], str] | None = None,
    ):
        workers = max_workers if max_workers is not None else get_settings().research_max_workers
        super().__init__(
            conninfo, max_workers=workers, display=display,
            conninfo_factory=conninfo_factory,
        )
        self._slugs = slugs
        self._client: OpenAI | None = None

    def on_phase_start(self) -> None:
        # Build one client for the phase. The openai SDK calls the bearer-token
        # provider per request, so AAD token refresh keeps working.
        self._client = build_research_client(get_settings())

    def item_key(self, item: ResearchWorkItem) -> str:
        return item["slug"]

    def item_label(self, item: ResearchWorkItem) -> str:
        return f"{item['slug']} ({item['name']})"

    def count_remaining(self) -> int:
        return self._run_reader_query(
            lambda r: r.count_companies_needing_bio(slugs=self._slugs)
        )

    def poll_work(self, batch_size: int) -> list[ResearchWorkItem]:
        return self._run_reader_query(
            lambda r: r.get_companies_needing_bio(
                limit=batch_size, slugs=self._slugs,
            )
        )

    def process_item(self, item: ResearchWorkItem) -> None:
        slug = item["slug"]
        name = item["name"]
        try:
            bio = research_company(name, client=self._client)
        except PermanentResearchError as e:
            log.warning("Research permanently failed for %s: %s — skipping", slug, e)
            self.display.record_error()
            self.display.advance(detail=f"{slug}: skipped ({e})")
            return  # No re-raise → no retry. Counted as one error.

        self.submit_write(
            lambda store, sl=slug, b=bio: store.update_company_bio(
                sl, short_bio=b.short_bio, long_bio=b.long_bio, model=b.model,
            )
        )

        # Embed long_bio so the company is immediately queryable via
        # find_companies. Bio write and embedding write are independent
        # UPDATEs on the same row — the WriteQueue serialises them.
        try:
            vector_literal, _tokens = embed_text(bio.long_bio)
            self.submit_write(
                lambda store, sl=slug, v=vector_literal:
                    store.update_company_embedding(sl, embedding=v)
            )
        except Exception:
            log.warning(
                "Embedding failed for %s — bio saved, re-run "
                "`jsb research-companies --company %s --force` to retry",
                slug, slug, exc_info=True,
            )

        self.display.add_to_info_counter(bio.web_search_count, label=" search")
        self.display.advance(detail=f"{slug} ({bio.web_search_count} searches)")
