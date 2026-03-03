"""Semantic job search via sqlite-vec + OpenAI-compatible embeddings."""

from dataclasses import dataclass
from pathlib import Path

from jobbuddy.embeddings import embed_query, serialize_f32
from jobbuddy.store import JobStore


@dataclass
class SearchResult:
    score: float | None
    job: dict


class VectorSearch:
    def __init__(self, db_path: Path | str | None = None):
        self.store = JobStore(db_path)

    def search(
        self,
        *,
        query: str | None = None,
        company: str | None = None,
        title: str | None = None,
        location: str | None = None,
        posted_after: str | None = None,
        limit: int = 25,
    ) -> list[SearchResult]:
        """Unified search: semantic when query is provided, keyword filters otherwise.

        With query: embeds the query and does KNN search, optionally filtered
        by company/title/location/posted_after.
        Without query: falls back to SQL LIKE filters via query_jobs().
        """
        if query:
            query_vec = embed_query(query)
            query_blob = serialize_f32(query_vec)
            rows = self.store.search_similar_filtered(
                query_blob,
                company=company,
                title=title,
                location=location,
                posted_after=posted_after,
                k=limit,
            )
            return [
                SearchResult(score=1.0 - row["distance"], job=row)
                for row in rows
            ]
        else:
            rows = self.store.query_jobs(
                company=company,
                title=title,
                location=location,
                posted_after=posted_after,
                limit=limit,
            )
            return [SearchResult(score=None, job=row) for row in rows]

    def close(self) -> None:
        self.store.close()
