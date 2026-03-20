"""Semantic job search via pgvector + OpenAI-compatible embeddings."""

from dataclasses import dataclass

from jobbuddy.embeddings import embed_query
from jobbuddy.store import JobStore


@dataclass
class SearchResult:
    score: float | None
    job: dict


class VectorSearch:
    def __init__(self, conninfo: str | None = None):
        self.store = JobStore(conninfo)
        self.store.conn.execute("SET hnsw.iterative_scan = relaxed_order")

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
        if query:
            query_vec = embed_query(query)
            rows = self.store.search_similar_filtered(
                query_vec,
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
