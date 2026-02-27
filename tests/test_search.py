"""Tests for jobbuddy.search — VectorSearch class."""

import struct
from unittest.mock import patch

import numpy as np
import pytest

from jobbuddy.models import Job
from jobbuddy.search import SearchResult, VectorSearch


def _make_job(id: str = "123", title: str = "PM", location: str = "Seattle", **kw) -> Job:
    return Job(
        id=id,
        title=title,
        location=location,
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        **kw,
    )


def _fake_vec(dimensions: int, seed: int = 0) -> np.ndarray:
    """Deterministic normalized vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dimensions).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


DIMS = 1536  # matches vec_jobs table definition


@pytest.fixture
def vs():
    """VectorSearch with in-memory store, pre-populated with jobs and embeddings."""
    search = VectorSearch(":memory:")
    store = search.store

    # Insert jobs with descriptions
    store.upsert_jobs("acme", [
        _make_job("1", "AI Product Manager", "Seattle", description="Lead AI product strategy and roadmap."),
        _make_job("2", "Software Engineer", "Remote", description="Build backend services and APIs."),
        _make_job("3", "Data Scientist", "NYC", description="ML models for user recommendations."),
    ])

    # Set description_stripped (required for embeddings to be useful in search)
    for jid in ["1", "2", "3"]:
        store.conn.execute(
            "UPDATE jobs SET description_stripped = description WHERE job_id = ?", (jid,)
        )
    store.conn.commit()

    for job_id_str in ["1", "2", "3"]:
        row = store.conn.execute(
            "SELECT id FROM jobs WHERE job_id = ?", (job_id_str,)
        ).fetchone()
        vec = _fake_vec(DIMS, seed=int(job_id_str))
        blob = struct.pack(f"<{len(vec)}f", *vec)
        store.store_embedding(row["id"], blob)

    yield search
    search.close()


class TestVectorSearch:
    def test_search_returns_results(self, vs):
        """Search returns ranked SearchResult objects."""
        query_vec = _fake_vec(DIMS, seed=1).tolist()  # same seed as job "1"
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="AI product strategy")
        assert len(results) > 0
        assert isinstance(results[0], SearchResult)
        assert results[0].score > 0
        assert results[0].job["title"] is not None

    def test_search_respects_limit(self, vs):
        query_vec = _fake_vec(DIMS, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="anything", limit=1)
        assert len(results) == 1

    def test_search_ranked_by_similarity(self, vs):
        """Results are sorted by descending similarity."""
        query_vec = _fake_vec(DIMS, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="anything", limit=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_excludes_disappeared(self, vs):
        """Disappeared jobs don't appear in results."""
        vs.store.upsert_jobs("acme", [])
        query_vec = _fake_vec(DIMS, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="anything")
        assert len(results) == 0

    def test_search_empty_embeddings(self):
        """Search with no embeddings returns empty."""
        vs = VectorSearch(":memory:")
        query_vec = _fake_vec(DIMS, seed=0).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="anything")
        assert results == []
        vs.close()


class TestVectorSearchFiltered:
    """Tests for combined semantic + keyword filtering."""

    def test_search_with_query_and_company_filter(self, vs):
        """Semantic search filtered by company."""
        # Add a job under a different company
        vs.store.upsert_jobs("beta", [
            _make_job("10", "AI Engineer", "Seattle", description="AI systems."),
        ])
        vs.store.conn.execute(
            "UPDATE jobs SET description_stripped = description WHERE job_id = '10'"
        )
        vs.store.conn.commit()
        row = vs.store.conn.execute("SELECT id FROM jobs WHERE job_id = '10'").fetchone()
        vec = _fake_vec(DIMS, seed=1)
        blob = struct.pack(f"<{len(vec)}f", *vec)
        vs.store.store_embedding(row["id"], blob)

        query_vec = _fake_vec(DIMS, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="AI", company="beta")
        assert len(results) == 1
        assert results[0].job["company_slug"] == "beta"

    def test_search_without_query_falls_back_to_keyword(self, vs):
        """Without query, search uses keyword filters via query_jobs."""
        results = vs.search(title="Software Engineer")
        assert len(results) == 1
        assert results[0].job["title"] == "Software Engineer"
        assert results[0].score is None  # no semantic score

    def test_search_without_query_filters_location(self, vs):
        """Without query, location filter works."""
        results = vs.search(location="Remote")
        assert len(results) == 1
        assert results[0].job["title"] == "Software Engineer"

    def test_search_without_query_all_jobs(self, vs):
        """Without query or filters, returns all jobs."""
        results = vs.search(limit=100)
        assert len(results) == 3
