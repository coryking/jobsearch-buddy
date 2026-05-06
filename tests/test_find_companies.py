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


class TestHybridSearch:
    def test_vector_arm_orders_by_cosine(self, store):
        _seed_company_with_bio(store, "acme", long_bio="x", name="Acme Corp",
                               short_bio="acme makes widgets")
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc",
                               short_bio="beta sells socks")
        _seed_company_with_bio(store, "good", long_bio="z", name="Good Co",
                               short_bio="good cooks food")

        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0, 0.0]))
        store.update_company_embedding("beta", embedding=_vec([0.0, 1.0, 0.0]))
        store.update_company_embedding("good", embedding=_vec([0.0, 0.0, 1.0]))

        # Query string is irrelevant to the vector ordering; FTS may or may
        # not match. The vector arm should still place acme first.
        rows = store.find_companies(_vec([1.0, 0.0, 0.0]),
                                    "totally unrelated query string", limit=3)
        assert rows[0]["slug"] == "acme"
        assert math.isclose(float(rows[0]["vec_score"]), 1.0, abs_tol=1e-6)

    def test_fts_arm_surfaces_exact_name(self, store):
        # Two companies with bios; "stripe" appears only in one's name.
        _seed_company_with_bio(store, "acme", long_bio="x", name="Stripe Inc",
                               short_bio="payment infrastructure")
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc",
                               short_bio="something else entirely")

        # Query embedding is orthogonal to acme's — vector arm would lose.
        store.update_company_embedding("acme", embedding=_vec([0.0, 1.0]))
        store.update_company_embedding("beta", embedding=_vec([1.0, 0.0]))

        rows = store.find_companies(_vec([1.0, 0.0]), "stripe", limit=2)
        # FTS arm rescues acme via the name match
        assert rows[0]["slug"] == "acme"
        assert rows[0]["fts_score"] is not None
        assert rows[0]["fts_score"] > 0

    def test_excludes_companies_without_embedding_unless_fts_matches(self, store):
        # acme has embedding; beta has bio + name match but no embedding.
        # FTS arm operates on companies regardless of embedding presence.
        _seed_company_with_bio(store, "acme", long_bio="x", name="Acme Corp",
                               short_bio="we make widgets")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc",
                               short_bio="we sell socks")

        # Query "widgets" hits acme via FTS only; acme also wins on vector
        # against the same query embedding (since beta has no embedding).
        rows = store.find_companies(_vec([1.0, 0.0]), "widgets", limit=10)
        slugs = [r["slug"] for r in rows]
        assert "acme" in slugs
        # beta should appear only if the FTS arm matched it
        if "beta" in slugs:
            beta_row = next(r for r in rows if r["slug"] == "beta")
            assert beta_row["vec_score"] is None  # no embedding
            assert beta_row["fts_score"] is not None

    def test_returns_short_bio_and_name(self, store):
        _seed_company_with_bio(
            store, "acme", long_bio="long", short_bio="we make widgets",
            name="Acme Corp",
        )
        store.update_company_embedding("acme", embedding=_vec([1.0]))
        rows = store.find_companies(_vec([1.0]), "widgets", limit=1)
        assert rows[0]["short_bio"] == "we make widgets"
        assert rows[0]["name"] == "Acme Corp"

    def test_rrf_score_combines_both_arms(self, store):
        # acme matches both arms; beta matches only vector. acme should win.
        _seed_company_with_bio(store, "acme", long_bio="x", name="Acme Corp",
                               short_bio="we make widgets")
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc",
                               short_bio="totally different content")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        store.update_company_embedding("beta", embedding=_vec([0.9, 0.0]))

        rows = store.find_companies(_vec([1.0, 0.0]), "widgets", limit=2)
        assert rows[0]["slug"] == "acme"
        # acme has both vec and fts contributions; rrf should beat beta's vec-only
        assert float(rows[0]["rrf_score"]) > float(rows[1]["rrf_score"])


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

    def test_returns_top_n_with_scores(self, store, patch_embed):
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
        # vec_score from the patched embedding ([1,0]) hits acme's vector
        assert math.isclose(top["vec_score"], 1.0, abs_tol=1e-3)
        # rrf_score is always present and positive when matched
        assert top["rrf_score"] is not None
        assert top["rrf_score"] > 0
        assert result["coverage_hint"] is None

    def test_coverage_hint_when_both_arms_miss(self, store, patch_embed):
        # Vector orthogonal to patched embed AND short_bio doesn't contain
        # the query tokens → both arms miss → hint fires.
        _seed_company_with_bio(
            store, "acme", long_bio="x", short_bio="boring placeholder text",
            name="Acme Corp",
        )
        store.update_company_embedding("acme", embedding=_vec([0.0, 0.0, 1.0]))
        result = core.find_companies("totally different query", limit=1)
        assert result["coverage_hint"] is not None
        assert "fall back to web" in result["coverage_hint"].lower()

    def test_coverage_hint_suppressed_when_fts_arm_matches(self, store, patch_embed):
        # Vector arm is weak (orthogonal) but FTS hits the company name.
        # Coverage hint should NOT fire: cache demonstrably has the entity.
        _seed_company_with_bio(
            store, "acme", long_bio="x", short_bio="payments infrastructure",
            name="Acme Corp",
        )
        store.update_company_embedding("acme", embedding=_vec([0.0, 0.0, 1.0]))
        result = core.find_companies("payments", limit=1)
        assert result["coverage_hint"] is None
        assert result["results"][0]["fts_score"] is not None
