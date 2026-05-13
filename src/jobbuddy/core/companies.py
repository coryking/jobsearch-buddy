"""Company resolution and the find_companies hybrid search.

`find_companies` is the entry point for vibe / kind-of-company search; the
slug-resolution helpers underpin every search path that lets the caller pass
display names instead of slugs.
"""

import logging

from jobbuddy.registry import list_companies, lookup_by_name

log = logging.getLogger(__name__)


def resolve_company_slugs(names: list[str]) -> list[str]:
    """Resolve a list of company names/slugs to canonical slugs.

    Raises ValueError naming the unknown entry. Used by both CLI and MCP so
    the failure mode is consistent.
    """
    slugs = []
    for name in names:
        resolved = lookup_by_name(name)
        if not resolved:
            available = ", ".join(c.name for c in list_companies().values())
            raise ValueError(f"Unknown company '{name}'. Registered: {available}")
        slugs.append(resolved.slug)
    return slugs


def resolve_exclude_companies(exclude_csv: str) -> list[str]:
    """Parse comma-separated company names/slugs into resolved slug list.

    Unknown entries pass through as-is rather than raising — exclude lists
    are best-effort and a typo shouldn't block the whole search.
    """
    slugs = []
    for name in exclude_csv.split(","):
        name = name.strip()
        if not name:
            continue
        resolved = lookup_by_name(name)
        slugs.append(resolved.slug if resolved else name)
    return slugs


def find_companies(
    query: str,
    *,
    limit: int = 20,
    coverage_floor: float = 0.35,
) -> dict:
    """Hybrid search over registered companies — vector + FTS, fused via RRF.

    Returns `{"results": [{slug, name, short_bio, active_jobs}],
    "coverage_hint": str | None}`. `active_jobs` is the count of jobs with
    `listing_status = 'active'` for the company. The MCP tool wrapper
    additionally attaches a per-account `applications` count to each
    result row before serializing — that field is account-scoped and not
    produced by this function.

    Internal vec/fts/rrf scores drive ordering but aren't returned here —
    use `jsb search-debug` to inspect them.

    `coverage_hint` is set when neither arm produced a strong match:
    vector below `coverage_floor` AND no FTS match. That tells the caller
    the named entity may not be a registered company and a web search is
    the right fallback.

    Always returns top-N rows when any embeddings exist; never returns
    empty just because the query is unusual. Raises ValueError when query
    is empty, limit < 1, or no company embeddings exist yet.
    """
    import psycopg

    from jobbuddy.embeddings import embed_text
    from jobbuddy.store import JobStore

    if not query or not query.strip():
        raise ValueError("find_companies requires a non-empty query")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    embedding, _tokens = embed_text(query)

    store = JobStore()
    try:
        rows = store.find_companies(embedding, query, limit=limit)
    except psycopg.Error as e:
        # websearch_to_tsquery is generous but malformed input still raises.
        # Re-raise as ValueError so MCP/CLI handlers can map it cleanly.
        raise ValueError(f"Invalid query for find_companies: {e}") from e
    finally:
        store.close()

    if not rows:
        raise ValueError(
            "No company embeddings yet. Run `jsb research-companies`"
            " to fill bios + embeddings."
        )

    top_vec = max((r["vec_score"] for r in rows if r["vec_score"] is not None), default=None)
    top_fts = max((r["fts_score"] for r in rows if r["fts_score"] is not None), default=None)

    # Coverage hint fires only when *both* arms missed: vector below floor
    # AND no FTS match at all. ts_rank is bounded differently than cosine
    # similarity, and any FTS hit means the query's tokens literally appear
    # in some company's name+short_bio — that's positive evidence the
    # entity is registered, even when the score is small.
    # The FTS CTE's WHERE already guarantees only real matches reach this
    # row, so any non-None fts_score is positive evidence of registration —
    # even ts_rank == 0.0 (which can occur on very short docs).
    coverage_hint = None
    vec_weak = top_vec is None or top_vec < coverage_floor
    fts_missed = top_fts is None
    if vec_weak and fts_missed:
        coverage_hint = (
            "Top match is weak — the named entity may not be a registered"
            " company. For exact-name lookups, fall back to web search."
        )

    def round4(v):
        return None if v is None else round(float(v), 4)

    results = [
        {
            "slug": r["slug"],
            "name": r["name"],
            "short_bio": r["short_bio"],
            "active_jobs": int(r["active_jobs"]),
        }
        for r in rows
    ]

    # Audit log so a few weeks of real traffic answer the open Phase 2
    # questions: is the coverage_floor calibrated correctly, and does the
    # FTS arm earn its keep on real queries (the deferred tuning question
    # from issue #41)? Scores aren't exposed in the response anymore but
    # still belong in logs.
    top_slugs = [r["slug"] for r in results[:3]]
    top_rrf = next((round4(r["rrf_score"]) for r in rows), None)
    log.info(
        "find_companies q=%r top_vec=%s top_fts=%s top_slugs=%s "
        "coverage_hint=%s n=%d",
        query, round4(top_vec), round4(top_fts), top_slugs,
        bool(coverage_hint), len(results),
        extra={
            "find_companies_query": query,
            "find_companies_top_vec": round4(top_vec),
            "find_companies_top_fts": round4(top_fts),
            "find_companies_top_rrf": top_rrf,
            "find_companies_top_slugs": top_slugs,
            "find_companies_coverage_hint": bool(coverage_hint),
            "find_companies_result_count": len(results),
        },
    )

    return {
        "results": results,
        "coverage_hint": coverage_hint,
    }
