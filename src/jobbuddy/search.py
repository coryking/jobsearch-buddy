"""Semantic job search via sqlite-vec + Azure OpenAI embeddings."""

from dataclasses import dataclass
from pathlib import Path

from jobbuddy.embeddings import embed_query, serialize_f32
from jobbuddy.store import JobStore


@dataclass
class SearchResult:
    score: float
    job: dict


class VectorSearch:
    def __init__(self, db_path: Path | str | None = None):
        self.store = JobStore(db_path)

    def search(self, query: str, limit: int = 25) -> list[SearchResult]:
        """Semantic search. Embeds query via Azure, KNN via sqlite-vec."""
        query_vec = embed_query(query)
        query_blob = serialize_f32(query_vec)
        rows = self.store.search_similar(query_blob, k=limit)
        return [
            SearchResult(score=1.0 - row["distance"], job=row)
            for row in rows
            if row.get("disappeared_at") is None
        ]

    def close(self) -> None:
        self.store.close()
