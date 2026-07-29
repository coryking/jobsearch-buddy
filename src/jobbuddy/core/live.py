"""Stateless live board listing — straight from the ATS, no jobs table.

`list_company_jobs_live` is the corpus-free counterpart to the stored-search
path: resolve a company, hit its board API right now, return compact rows.
Freshness is absolute (the data is seconds old) and the failure mode is an
explicit error instead of a silently stale row.

Rows deliberately exclude `description` — a full Ashby board carries every
JD inline, and dumping hundreds of them would swamp the calling LLM. The
per-job detail path (`fetch_from_url` / `fetch_by_id`) returns the full JD.
"""

from jobbuddy.fetchers import SUPPORTED_ATS_TYPES, get_fetcher
from jobbuddy.registry import lookup_by_name

# Fields a listing row carries. Everything else on Job (description,
# ats_metadata) is detail-fetch territory.
_ROW_FIELDS = (
    "id", "title", "location", "department", "team",
    "salary", "published_at", "url",
)


def list_company_jobs_live(
    company: str,
    *,
    published_since: str = "",
    limit: int = 500,
) -> dict:
    """Live-fetch a company's job board and return compact listing rows.

    `published_since` (e.g. '3d', '1w') keeps rows published on/after the
    cutoff — rows with no publish date survive the filter (unknown is not
    old). `total` is always the full board size before filtering, so the
    caller can tell "quiet board" from "filtered down".

    Raises ValueError for unknown companies or companies with no usable
    board config.
    """
    resolved = lookup_by_name(company)
    if not resolved:
        raise ValueError(
            f"Unknown company: {company}. Pass a registered company slug or "
            "name, or fetch a specific posting by URL to auto-register it."
        )
    if not resolved.ats or resolved.ats not in SUPPORTED_ATS_TYPES:
        raise ValueError(
            f"No job board configured for {resolved.name}. "
            "Fetch a specific posting by URL instead — it auto-detects the ATS."
        )

    fetcher = get_fetcher(resolved)
    jobs = fetcher.list_jobs()
    total = len(jobs)

    if published_since:
        from jobbuddy.core.durations import parse_duration_to_date

        cutoff = parse_duration_to_date(published_since)
        jobs = [j for j in jobs if j.published_at is None or str(j.published_at) >= cutoff]

    # Newest first; undated rows sort as "" which lands last under reverse.
    jobs.sort(key=lambda j: str(j.published_at) if j.published_at else "", reverse=True)

    rows = []
    for j in jobs[:limit]:
        dumped = j.model_dump()
        rows.append({
            k: dumped[k] for k in _ROW_FIELDS
            if dumped.get(k) not in (None, "")
        })

    return {
        "company": resolved.slug,
        "company_name": resolved.name,
        "ats": resolved.ats,
        "total": total,
        "returned": len(rows),
        "rows": rows,
    }
