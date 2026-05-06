"""Tests for the company-embedding pipeline + find_companies surface.

Covers:
- JobStore.{count,get}_companies_needing_embedding predicate (column-presence,
  stale relative to bio_researched_at)
- JobStore.update_company_embedding write path
- JobStore.find_companies_by_vector ranking (cosine score order, score range)
- core.find_companies validation, coverage_hint trigger, raise on empty cache

The embedding call is monkeypatched — no live OpenAI traffic in tests.
"""

from __future__ import annotations

import math
from typing import Iterable

import pytest

from jobbuddy import core, embeddings


def _vec(values: Iterable[float]) -> str:
    """Build a pgvector text literal from raw floats. Pads to 1536 dims."""
    out = list(values)
    if len(out) < embeddings.DIMENSIONS:
        out = out + [0.0] * (embeddings.DIMENSIONS - len(out))
    return embeddings.vector_literal(out)


def _seed_company_with_bio(store, slug: str, *, long_bio: str, name: str | None = None,
                           short_bio: str = "stub bio") -> None:
    """Fill bio fields on a pre-seeded test company. bio_researched_at = now()."""
    store.conn.execute(
        """UPDATE companies SET
            short_bio = %s,
            long_bio = %s,
            bio_model = 'test',
            bio_researched_at = now()
           WHERE slug = %s""",
        (short_bio, long_bio, slug),
    )
    if name is not None:
        store.conn.execute("UPDATE companies SET name = %s WHERE slug = %s", (name, slug))


class TestEmbeddingPredicate:
    def test_no_long_bio_means_not_needing(self, store):
        # acme has no bio → not selected
        assert store.count_companies_needing_embedding() == 0
        assert store.get_companies_needing_embedding() == []

    def test_bio_without_embedding_is_needing(self, store):
        _seed_company_with_bio(store, "acme", long_bio="Acme builds widgets.")
        assert store.count_companies_needing_embedding() == 1
        items = store.get_companies_needing_embedding()
        assert len(items) == 1
        assert items[0]["slug"] == "acme"
        assert items[0]["long_bio"] == "Acme builds widgets."

    def test_fresh_embedding_is_not_needing(self, store):
        _seed_company_with_bio(store, "acme", long_bio="Acme.")
        store.update_company_embedding("acme", embedding=_vec([0.1, 0.2]))
        assert store.count_companies_needing_embedding() == 0

    def test_stale_embedding_is_needing(self, store):
        _seed_company_with_bio(store, "acme", long_bio="Acme.")
        store.update_company_embedding("acme", embedding=_vec([0.1]))
        # Backdate the embedding to before the current bio_researched_at
        store.conn.execute(
            "UPDATE companies SET bio_embedding_updated_at ="
            " bio_researched_at - interval '1 hour' WHERE slug = 'acme'"
        )
        assert store.count_companies_needing_embedding() == 1

    def test_slug_filter_scopes_predicate(self, store):
        _seed_company_with_bio(store, "acme", long_bio="A.")
        _seed_company_with_bio(store, "beta", long_bio="B.")
        items = store.get_companies_needing_embedding(slugs=["acme"])
        assert [i["slug"] for i in items] == ["acme"]


class TestVectorSearch:
    def test_orders_by_cosine_similarity(self, store):
        _seed_company_with_bio(store, "acme", long_bio="x", name="Acme Corp")
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc")
        _seed_company_with_bio(store, "good", long_bio="z", name="Good Co")

        # Three orthogonal-ish vectors; query is closest to acme's.
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0, 0.0]))
        store.update_company_embedding("beta", embedding=_vec([0.0, 1.0, 0.0]))
        store.update_company_embedding("good", embedding=_vec([0.0, 0.0, 1.0]))

        rows = store.find_companies_by_vector(_vec([1.0, 0.0, 0.0]), limit=3)
        assert [r["slug"] for r in rows] == ["acme", "beta", "good"]
        # Score is cosine similarity; top should be ~1.0
        assert math.isclose(float(rows[0]["score"]), 1.0, abs_tol=1e-6)
        # Orthogonal vectors → ~0.0
        assert math.isclose(float(rows[1]["score"]), 0.0, abs_tol=1e-6)

    def test_excludes_companies_without_embedding(self, store):
        _seed_company_with_bio(store, "acme", long_bio="x")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        # beta has bio but no embedding
        _seed_company_with_bio(store, "beta", long_bio="y")

        rows = store.find_companies_by_vector(_vec([1.0, 0.0]), limit=10)
        assert [r["slug"] for r in rows] == ["acme"]

    def test_returns_short_bio_and_name(self, store):
        _seed_company_with_bio(
            store, "acme", long_bio="long", short_bio="we make widgets", name="Acme Corp",
        )
        store.update_company_embedding("acme", embedding=_vec([1.0]))
        rows = store.find_companies_by_vector(_vec([1.0]), limit=1)
        assert rows[0]["short_bio"] == "we make widgets"
        assert rows[0]["name"] == "Acme Corp"


class TestCoreFindCompanies:
    @pytest.fixture
    def patch_embed(self, monkeypatch):
        """Replace embed_text with a deterministic stub. Returns the recorded
        call list so individual tests can assert query routing."""
        calls: list[str] = []

        def fake_embed(text: str) -> tuple[str, int]:
            calls.append(text)
            # Map 'acme'-ish queries to acme's vector, etc.
            if "acme" in text.lower():
                return _vec([1.0, 0.0]), 1
            return _vec([0.0, 1.0]), 1

        # Patch in both the embeddings module and any cached import path
        monkeypatch.setattr("jobbuddy.embeddings.embed_text", fake_embed)
        monkeypatch.setattr("jobbuddy.core.embed_text", fake_embed, raising=False)
        return calls

    def test_empty_query_raises(self, patch_embed):
        with pytest.raises(ValueError, match="non-empty query"):
            core.find_companies("")
        with pytest.raises(ValueError, match="non-empty query"):
            core.find_companies("   ")

    def test_invalid_limit_raises(self, patch_embed):
        with pytest.raises(ValueError, match="limit"):
            core.find_companies("anything", limit=0)

    def test_empty_cache_raises(self, patch_embed):
        # No company embeddings seeded
        with pytest.raises(ValueError, match="No company embeddings"):
            core.find_companies("anything")

    def test_returns_top_n_with_score(self, store, patch_embed):
        _seed_company_with_bio(store, "acme", long_bio="x", short_bio="we make widgets",
                               name="Acme Corp")
        _seed_company_with_bio(store, "beta", long_bio="y", short_bio="we sell socks",
                               name="Beta Inc")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        store.update_company_embedding("beta", embedding=_vec([0.0, 1.0]))

        result = core.find_companies("acme please", limit=2)
        assert len(result["results"]) == 2
        top = result["results"][0]
        assert top["slug"] == "acme"
        assert top["name"] == "Acme Corp"
        assert top["short_bio"] == "we make widgets"
        assert math.isclose(top["score"], 1.0, abs_tol=1e-3)
        assert result["coverage_hint"] is None

    def test_coverage_hint_when_top_score_below_floor(self, store, patch_embed):
        # Seed a company with a vector orthogonal to anything patch_embed will
        # produce. Cosine ≈ 0 < default floor 0.35 → hint fires.
        _seed_company_with_bio(store, "acme", long_bio="x")
        store.update_company_embedding("acme", embedding=_vec([0.0, 0.0, 1.0]))
        result = core.find_companies("totally different query", limit=1)
        assert result["coverage_hint"] is not None
        assert "fall back to web" in result["coverage_hint"].lower()
