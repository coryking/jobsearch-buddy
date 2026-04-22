"""Tests for jobbuddy.search -- VectorSearch class (PostgreSQL)."""

from unittest.mock import patch

import numpy as np
import pytest

from jobbuddy.search import SearchResult, VectorSearch
from jobbuddy.store import JobStore

from conftest import make_job


def _fake_vec(dimensions: int, seed: int = 0) -> np.ndarray:
    """Deterministic normalized vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dimensions).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


DIMS = 1536


@pytest.fixture
def vs(pg_conninfo):
    """VectorSearch pre-populated with jobs and embeddings."""
    search = VectorSearch(pg_conninfo)
    store = search.store

    # Insert jobs with descriptions
    store.upsert_jobs("acme", [
        make_job("1", "AI Product Manager", "Seattle", description="Lead AI product strategy and roadmap."),
        make_job("2", "Software Engineer", "Remote", description="Build backend services and APIs."),
        make_job("3", "Data Scientist", "NYC", description="ML models for user recommendations."),
    ])

    for jid in ["1", "2", "3"]:
        store.conn.execute(
            "UPDATE jobs SET description_stripped = description WHERE job_id = %s", (jid,)
        )
    # Recompute content_hash with stripped description
    store.conn.execute("""
        UPDATE jobs SET content_hash = md5(
            coalesce(description_stripped, '') || title || coalesce(location, '') || coalesce(department, '')
        )::uuid
        WHERE description_stripped IS NOT NULL
    """)

    company_hash = str(store.conn.execute(
        "SELECT content_hash FROM companies WHERE slug = 'acme'"
    ).fetchone()["content_hash"])

    for job_id_str in ["1", "2", "3"]:
        row = store.conn.execute(
            "SELECT id, content_hash FROM jobs WHERE job_id = %s", (job_id_str,)
        ).fetchone()
        vec = _fake_vec(DIMS, seed=int(job_id_str)).tolist()
        store.store_embedding(row["id"], str(row["content_hash"]), company_hash, vec)

    yield search
    search.close()


class TestVectorSearch:
    def test_search_returns_results(self, vs):
        """Search returns ranked SearchResult objects."""
        query_vec = _fake_vec(DIMS, seed=1).tolist()
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

    def test_search_excludes_removed(self, vs):
        """Removed jobs don't appear in results."""
        vs.store.upsert_jobs("acme", [])
        query_vec = _fake_vec(DIMS, seed=1).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = vs.search(query="anything")
        assert len(results) == 0

    def test_search_empty_embeddings(self, pg_conninfo):
        """Search with no embeddings returns empty."""
        search = VectorSearch(pg_conninfo)
        query_vec = _fake_vec(DIMS, seed=0).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = search.search(query="anything")
        assert results == []
        search.close()


class TestSearchDiversity:
    """Tests for round-robin diversity and relevance floor."""

    @pytest.fixture
    def multi_company_vs(self, pg_conninfo):
        """VectorSearch with jobs across 3 companies, volume-skewed."""
        search = VectorSearch(pg_conninfo)
        store = search.store

        # acme: 10 jobs (dominant), beta: 3 jobs, good: 2 jobs
        acme_jobs = [
            make_job(str(i), f"Acme Engineer {i}", "Seattle",
                     description=f"Acme engineering role {i}.")
            for i in range(100, 110)
        ]
        beta_jobs = [
            make_job(str(i), f"Beta Engineer {i}", "Seattle",
                     description=f"Beta engineering role {i}.")
            for i in range(200, 203)
        ]
        good_jobs = [
            make_job(str(i), f"Good Engineer {i}", "Seattle",
                     description=f"Good engineering role {i}.")
            for i in range(300, 302)
        ]

        store.upsert_jobs("acme", acme_jobs)
        store.upsert_jobs("beta", beta_jobs)
        store.upsert_jobs("good", good_jobs)

        store.conn.execute(
            "UPDATE jobs SET description_stripped = description "
            "WHERE description_stripped IS NULL AND description IS NOT NULL"
        )
        store.conn.execute("""
            UPDATE jobs SET content_hash = md5(
                coalesce(description_stripped, '') || title
                || coalesce(location, '') || coalesce(department, '')
            )::uuid WHERE description_stripped IS NOT NULL
        """)

        for slug in ["acme", "beta", "good"]:
            company_hash = str(store.conn.execute(
                "SELECT content_hash FROM companies WHERE slug = %s", (slug,)
            ).fetchone()["content_hash"])
            rows = store.conn.execute(
                "SELECT id, job_id, content_hash FROM jobs WHERE company_slug = %s",
                (slug,),
            ).fetchall()
            for row in rows:
                vec = _fake_vec(DIMS, seed=int(row["job_id"])).tolist()
                store.store_embedding(
                    row["id"], str(row["content_hash"]), company_hash, vec
                )

        yield search
        search.close()

    def test_diversity_round_robin_vector(self, multi_company_vs):
        """Vector search distributes results across companies, not dominated by acme."""
        query_vec = _fake_vec(DIMS, seed=100).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = multi_company_vs.search(query="engineering", limit=15)
        companies = [r.job["company_slug"] for r in results]
        assert "acme" in companies
        assert "beta" in companies
        assert "good" in companies
        assert companies.count("acme") < len(results)

    def test_diversity_round_robin_keyword(self, multi_company_vs):
        """Keyword search distributes results across companies."""
        results = multi_company_vs.search(title="Engineer", limit=15)
        companies = [r.job["company_slug"] for r in results]
        assert "acme" in companies
        assert "beta" in companies
        assert "good" in companies
        assert companies.count("acme") < len(results)

    def test_exclude_companies_vector(self, multi_company_vs):
        """Excluded companies don't appear in vector search results."""
        query_vec = _fake_vec(DIMS, seed=100).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = multi_company_vs.search(
                query="engineering", exclude_companies=["acme"], limit=15
            )
        companies = {r.job["company_slug"] for r in results}
        assert "acme" not in companies
        assert len(results) > 0

    def test_exclude_companies_keyword(self, multi_company_vs):
        """Excluded companies don't appear in keyword search results."""
        results = multi_company_vs.search(
            title="Engineer", exclude_companies=["acme", "beta"], limit=15
        )
        companies = {r.job["company_slug"] for r in results}
        assert "acme" not in companies
        assert "beta" not in companies
        assert "good" in companies

    def test_no_rn_in_results(self, multi_company_vs):
        """The internal rn column doesn't leak into result dicts."""
        query_vec = _fake_vec(DIMS, seed=100).tolist()
        with patch("jobbuddy.search.embed_query", return_value=query_vec):
            results = multi_company_vs.search(query="engineering", limit=5)
        for r in results:
            assert "rn" not in r.job

        results_kw = multi_company_vs.search(title="Engineer", limit=5)
        for r in results_kw:
            assert "rn" not in r.job

    def test_single_company_skips_diversity(self, multi_company_vs):
        """Single-company keyword search returns all from that company."""
        results = multi_company_vs.search(
            title="Engineer", company="acme", limit=100
        )
        companies = {r.job["company_slug"] for r in results}
        assert companies == {"acme"}
        assert len(results) == 10


class TestVectorSearchFiltered:
    """Tests for combined semantic + keyword filtering."""

    def test_search_with_query_and_company_filter(self, vs):
        """Semantic search filtered by company."""
        vs.store.upsert_jobs("beta", [
            make_job("10", "AI Engineer", "Seattle", description="AI systems."),
        ])
        vs.store.conn.execute(
            "UPDATE jobs SET description_stripped = description WHERE job_id = '10'"
        )
        vs.store.conn.execute("""
            UPDATE jobs SET content_hash = md5(
                coalesce(description_stripped, '') || title || coalesce(location, '') || coalesce(department, '')
            )::uuid WHERE job_id = '10'
        """)
        row = vs.store.conn.execute("SELECT id, content_hash FROM jobs WHERE job_id = '10'").fetchone()
        company_hash = str(vs.store.conn.execute(
            "SELECT content_hash FROM companies WHERE slug = 'beta'"
        ).fetchone()["content_hash"])
        vec = _fake_vec(DIMS, seed=1).tolist()
        vs.store.store_embedding(row["id"], str(row["content_hash"]), company_hash, vec)

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
        assert results[0].score is None

    def test_search_without_query_filters_location(self, vs):
        """Without query, location filter works."""
        results = vs.search(location="Remote")
        assert len(results) == 1
        assert results[0].job["title"] == "Software Engineer"

    def test_search_without_query_all_jobs(self, vs):
        """Without query or filters, returns all jobs."""
        results = vs.search(limit=100)
        assert len(results) == 3
