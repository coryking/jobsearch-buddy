"""Tests for the find_companies surface.

Covers:
- JobStore.update_company_embedding write path
- JobStore.find_companies hybrid (vector + FTS) ranking via RRF
- core.find_companies validation, coverage_hint trigger, empty-cache error
- ResearchPhase inline embedding: bio + embedding paired, embed failure
  swallowed without losing the bio write

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

    def test_returns_active_job_count(self, store, pg_conninfo):
        from tests.conftest import make_job, seed_jobs

        _seed_company_with_bio(store, "acme", long_bio="x", name="Acme Corp",
                               short_bio="we make widgets")
        _seed_company_with_bio(store, "beta", long_bio="y", name="Beta Inc",
                               short_bio="we sell socks")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        store.update_company_embedding("beta", embedding=_vec([0.0, 1.0]))

        seed_jobs(pg_conninfo, "acme", [
            make_job(id="1", title="PM"),
            make_job(id="2", title="Eng"),
            make_job(id="3", title="Design"),
        ])
        # Beta has no jobs.
        # Removed jobs should not count: insert one then mark removed.
        seed_jobs(pg_conninfo, "acme", [make_job(id="1", title="PM"),
                                        make_job(id="2", title="Eng")])
        # Job id=3 is now stale (not in latest upsert) → listing_status='removed'

        rows = store.find_companies(_vec([1.0, 0.0]), "widgets", limit=10)
        by_slug = {r["slug"]: r for r in rows}
        assert by_slug["acme"]["active_jobs"] == 2
        assert by_slug["beta"]["active_jobs"] == 0

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

    def test_returns_top_n_without_scores(self, store, patch_embed):
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
        assert top["active_jobs"] == 0
        # Scoring fields are dropped from the public response; use
        # `jsb search-debug` for tuning.
        assert "vec_score" not in top
        assert "fts_score" not in top
        assert "rrf_score" not in top
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
        assert result["results"][0]["slug"] == "acme"


class TestResearchPhaseInlineEmbedding:
    """ResearchPhase writes a bio AND embedding in a single process_item."""

    def _make_phase(self, monkeypatch, *, embed_raises: bool = False):
        from jobbuddy.research import CompanyBio
        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.research import ResearchPhase
        from tests.conftest import TEST_CONNINFO

        bio = CompanyBio(
            short_bio="we make widgets",
            long_bio="acme makes widgets for warehouses",
            model="gpt-test",
            web_search_count=2,
        )
        monkeypatch.setattr(
            "jobbuddy.sync.research.research_company",
            lambda name, client=None: bio,
        )

        embed_calls: list[str] = []

        def fake_embed(text):
            embed_calls.append(text)
            if embed_raises:
                raise RuntimeError("embedding API down")
            return _vec([0.5, 0.5]), 7

        monkeypatch.setattr("jobbuddy.sync.research.embed_text", fake_embed)

        phase = ResearchPhase(TEST_CONNINFO, display=PhaseState("Research"))
        return phase, bio, embed_calls

    def _run_process_item(self, phase, store):
        from jobbuddy.sync.base import WriteQueue
        phase._writer = WriteQueue(conninfo_factory=lambda: store.conn.info.dsn)
        phase._writer.start()
        try:
            phase.process_item({"slug": "acme", "name": "Acme Corp"})
            phase._writer.flush()
        finally:
            phase._writer.stop()

    def test_happy_path_writes_bio_and_embedding(self, store, monkeypatch):
        phase, bio, embed_calls = self._make_phase(monkeypatch)
        self._run_process_item(phase, store)

        assert embed_calls == [bio.long_bio]
        row = store.conn.execute(
            "SELECT short_bio, long_bio, bio_embedding IS NOT NULL AS has_emb"
            " FROM companies WHERE slug = 'acme'"
        ).fetchone()
        assert row["short_bio"] == bio.short_bio
        assert row["long_bio"] == bio.long_bio
        assert row["has_emb"] is True

    def test_embed_failure_does_not_lose_bio_write(self, store, monkeypatch):
        phase, bio, embed_calls = self._make_phase(monkeypatch, embed_raises=True)
        # Should NOT raise — the embed exception is swallowed by design
        self._run_process_item(phase, store)

        assert embed_calls == [bio.long_bio]
        row = store.conn.execute(
            "SELECT long_bio, bio_embedding IS NOT NULL AS has_emb"
            " FROM companies WHERE slug = 'acme'"
        ).fetchone()
        # Bio landed, embedding didn't — the recovery contract.
        assert row["long_bio"] == bio.long_bio
        assert row["has_emb"] is False


class TestFindCompaniesAtsFilter:
    """`ats` narrows the result set to companies whose `companies.ats`
    value matches (post-normalization). Liberal matching: case +
    punctuation insensitive. Unknown values silently drop. Compound DB
    values like `oracle_hcm` match `oracle-hcm` / `Oracle HCM` / `oracle_hcm`."""

    @pytest.fixture
    def patch_embed(self, monkeypatch):
        def fake_embed(text: str) -> tuple[str, int]:
            return _vec([1.0, 0.0]), 1

        monkeypatch.setattr("jobbuddy.embeddings.embed_text", fake_embed)
        monkeypatch.setattr("jobbuddy.core.embed_text", fake_embed, raising=False)
        return None

    def _seed_pair(self, store):
        # acme is greenhouse (per the session fixture); workday-co is workday.
        _seed_company_with_bio(store, "acme", long_bio="x", short_bio="we make widgets",
                               name="Acme Corp")
        _seed_company_with_bio(store, "workday-co", long_bio="y", short_bio="we make widgets",
                               name="Workday Co")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))
        store.update_company_embedding("workday-co", embedding=_vec([1.0, 0.0]))

    def test_baseline_no_filter(self, store, patch_embed):
        self._seed_pair(store)
        result = core.find_companies("widgets", limit=10)
        slugs = {r["slug"] for r in result["results"]}
        assert {"acme", "workday-co"}.issubset(slugs)

    def test_filter_to_greenhouse(self, store, patch_embed):
        self._seed_pair(store)
        result = core.find_companies("widgets", limit=10, ats=["greenhouse"])
        slugs = {r["slug"] for r in result["results"]}
        assert slugs == {"acme"}

    def test_filter_to_two_atses(self, store, patch_embed):
        self._seed_pair(store)
        result = core.find_companies("widgets", limit=10, ats=["greenhouse", "workday"])
        slugs = {r["slug"] for r in result["results"]}
        assert slugs == {"acme", "workday-co"}

    def test_liberal_normalization(self, store, patch_embed):
        self._seed_pair(store)
        result = core.find_companies(
            "widgets", limit=10, ats=["Greenhouse, ", "WORKDAY"],
        )
        slugs = {r["slug"] for r in result["results"]}
        assert slugs == {"acme", "workday-co"}

    def test_unknown_ats_returns_empty(self, store, patch_embed):
        self._seed_pair(store)
        result = core.find_companies("widgets", limit=10, ats=["nonexistent"])
        assert result["results"] == []

    def test_compound_ats_underscore(self, store, patch_embed):
        from jobbuddy.models import Company
        store.save_company(Company(slug="oracle-co", name="Oracle Co",
                                   ats="oracle_hcm", board="oracle-co"))
        _seed_company_with_bio(store, "oracle-co", long_bio="x",
                               short_bio="we make widgets", name="Oracle Co")
        store.update_company_embedding("oracle-co", embedding=_vec([1.0, 0.0]))
        _seed_company_with_bio(store, "acme", long_bio="x",
                               short_bio="we make widgets", name="Acme Corp")
        store.update_company_embedding("acme", embedding=_vec([1.0, 0.0]))

        for variant in (["oracle_hcm"], ["oracle-hcm"], ["Oracle HCM"]):
            result = core.find_companies("widgets", limit=10, ats=variant)
            slugs = {r["slug"] for r in result["results"]}
            assert slugs == {"oracle-co"}, f"variant {variant!r} got {slugs}"
