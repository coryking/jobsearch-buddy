"""EmbedPhase — per-model embedding generation."""

from __future__ import annotations

import logging
from jobbuddy.embeddings import MODEL_REGISTRY, embed_texts_iter, get_model, serialize_f32, unload_models
from jobbuddy.store import JobStore
from jobbuddy.sync.types import (
    EventQueue,
    ModelLoaded,
    ModelUnloaded,
    Phase,
    PhaseDone,
    PhaseProgress,
    PhaseStarted,
)

log = logging.getLogger(__name__)


class EmbedPhase:
    def __init__(
        self,
        store: JobStore,
        slugs: list[str],
        events: EventQueue,
    ):
        self.store = store
        self.slugs = slugs
        self.events = events

    def run(self) -> None:
        """Generate embeddings for all models sequentially."""
        for i, config in enumerate(MODEL_REGISTRY.values()):
            # Free memory from the previous model before loading the next
            if i > 0:
                prev_config = list(MODEL_REGISTRY.values())[i - 1]
                self.events.put(ModelUnloaded(prev_config.model_key, prev_config.model_name, ""))
                unload_models()
            # Count jobs needing embeddings across synced companies
            total = 0
            for slug in self.slugs:
                total += self.store.jobs_needing_embeddings(
                    config.model_key, slug=slug, count_only=True
                )

            detail = f"{config.model_name}, {config.dimensions}d"
            self.events.put(PhaseStarted(Phase.EMBED, total, detail))

            if total > 0:
                # Trigger model load (lazy) so we can report it
                get_model(config.model_key)
                self.events.put(ModelLoaded(config.model_key, config.model_name, "onnx"))
                self._embed_model(config.model_key, total)

            self.events.put(PhaseDone(Phase.EMBED))

    def _embed_model(self, model_key: str, total: int) -> None:
        """Generate embeddings for a single model across all synced companies."""
        done = 0
        for slug in self.slugs:
            jobs = self.store.jobs_needing_embeddings(model_key, slug=slug, count_only=False)
            if not jobs:
                continue

            texts = [j["text"] for j in jobs]
            try:
                for job_info, vec in zip(jobs, embed_texts_iter(texts, model_key)):
                    blob = serialize_f32(vec)
                    self.store.store_embedding(
                        job_info["id"],
                        model_key,
                        blob,
                        job_info["text_hash"],
                    )
                    done += 1
                    self.events.put(PhaseProgress(Phase.EMBED, done, total))
            except Exception as e:
                log.warning("Embedding generation failed for %s/%s: %s", slug, model_key, e)
