"""Tests for jobbuddy.search — VectorSearch class."""

import hashlib
import struct
from unittest.mock import patch

import numpy as np
import pytest

from jobbuddy.models import Job
from jobbuddy.search import SearchResult, VectorSearch
from jobbuddy.store import JobStore


def _make_job(id: str = "123", title: str = "PM", location: str = "Seattle", **kw) -> Job:
    return Job(
        id=id,
        title=title,
        location=location,
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        **kw,
    )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fake_vec(dimensions: int, seed: int = 0) -> np.ndarray:
    """Deterministic normalized vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dimensions).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


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

    # Store embeddings for bge_small (384d)
    for job_id_str in ["1", "2", "3"]:
        row = store.conn.execute(
            "SELECT id, company_slug, description FROM jobs WHERE job_id = ?", (job_id_str,)
        ).fetchone()
        surr_id = row["id"]
        job = _make_job(job_id_str, description=row["description"])
        text = job.embed_text(row["company_slug"])
        h = _text_hash(text)
        vec = _fake_vec(384, seed=int(job_id_str))
        blob = struct.pack(f"<{len(vec)}f", *vec)
        store.store_embedding(surr_id, "bge_small", blob, h)

    yield search
    search.close()


class TestVectorSearch:
    def test_search_returns_results(self, vs):
        """Search returns ranked SearchResult objects."""
        # Mock embed_query to return a vector close to job 1's
        query_vec = _fake_vec(384, seed=1).tolist()  # same seed as job "1"
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search("AI product strategy")
        assert len(results) > 0
        assert isinstance(results[0], SearchResult)
        assert results[0].score > 0
        assert results[0].job["title"] is not None

    def test_search_respects_limit(self, vs):
        query_vec = _fake_vec(384, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search("anything", limit=1)
        assert len(results) == 1

    def test_search_ranked_by_similarity(self, vs):
        """Results are sorted by descending similarity."""
        query_vec = _fake_vec(384, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search("anything", limit=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_excludes_disappeared(self, vs):
        """Disappeared jobs don't appear in results."""
        # Make all jobs disappear
        vs.store.upsert_jobs("acme", [])
        query_vec = _fake_vec(384, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search("anything")
        assert len(results) == 0

    def test_search_empty_embeddings(self):
        """Search with no embeddings returns empty."""
        vs = VectorSearch(":memory:")
        query_vec = _fake_vec(384, seed=0).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search("anything")
        assert results == []
        vs.close()

    def test_available_models(self, vs):
        models = vs.available_models
        assert "bge_small" in models

    def test_search_all_models(self, vs):
        """search_all_models returns dict keyed by model."""
        query_vec = _fake_vec(384, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            all_results = vs.search_all_models("anything")
        assert "bge_small" in all_results
        assert len(all_results["bge_small"]) > 0
