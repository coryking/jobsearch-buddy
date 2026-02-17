"""VectorSearch — semantic search over job embeddings using NumPy cosine similarity.

Owns a JobStore and knows about the embedding model registry.
Search consumers (web.py, mcp_server.py) create one of these.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jobbuddy.embeddings import (
    DEFAULT_MODEL_KEY,
    MODEL_REGISTRY,
    EmbeddingModelConfig,
    embed_query,
)
from jobbuddy.store import JobStore


@dataclass
class SearchResult:
    """A single search result with score and job metadata."""

    score: float  # cosine similarity [0, 1]
    job: dict  # full job row from DB


class VectorSearch:
    """Semantic job search using per-model BLOB embeddings + NumPy cosine similarity."""

    def __init__(self, db_path: Path | str | None = None):
        self.store = JobStore(db_path)

    @property
    def available_models(self) -> dict[str, EmbeddingModelConfig]:
        return MODEL_REGISTRY

    def search(
        self,
        query: str,
        model_key: str = DEFAULT_MODEL_KEY,
        limit: int = 25,
    ) -> list[SearchResult]:
        """Search jobs by semantic similarity using a specific model."""
        query_vec = np.array(embed_query(query, model_key), dtype=np.float32)
        matrix, job_ids = self.store.load_embeddings(model_key)

        if matrix.shape[0] == 0:
            return []

        # Cosine similarity — vectors are normalized by sentence-transformers
        scores = matrix @ query_vec
        top_indices = np.argsort(scores)[::-1][:limit]

        top_job_ids = [job_ids[i] for i in top_indices]
        top_scores = [float(scores[i]) for i in top_indices]

        jobs_by_id = {j["id"]: j for j in self.store.get_job_by_ids(top_job_ids)}

        results = []
        for jid, score in zip(top_job_ids, top_scores):
            job = jobs_by_id.get(jid)
            if job and job.get("disappeared_at") is None:
                results.append(SearchResult(score=score, job=job))

        return results

    def search_all_models(
        self,
        query: str,
        limit: int = 25,
    ) -> dict[str, list[SearchResult]]:
        """Search across all models, returning results per model."""
        return {
            key: self.search(query, model_key=key, limit=limit)
            for key in MODEL_REGISTRY
        }

    def close(self) -> None:
        self.store.close()
