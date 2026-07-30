"""Stateless live board listing — straight from the ATS, no jobs table.

`list_company_jobs_live` is the corpus-free counterpart to the stored-search
path: resolve a company, hit its board API right now, return compact rows.
Freshness is absolute (the data is seconds old) and the failure mode is an
explicit error instead of a silently stale row.

Rows deliberately exclude `description` — a full Ashby board carries every
JD inline, and dumping hundreds of them would swamp the calling LLM. The
per-job detail path (`fetch_from_url` / `fetch_by_id`) returns the full JD.
"""

from datetime import date

from jobbuddy.fetchers import SUPPORTED_ATS_TYPES, get_fetcher
from jobbuddy.models import Job
from jobbuddy.registry import lookup_by_name

# Fields a listing row carries. Everything else on Job (description,
# ats_metadata) is detail-fetch territory. `last_listing_update`
# disambiguates a year-old `published_at` that the ATS touched yesterday
# (evergreen reqs — issue #53); the posted_since filter honors it too.
_ROW_FIELDS = (
    "id", "title", "location", "department", "team",
    "salary", "published_at", "last_listing_update", "url",
)


def job_freshness(j: Job) -> date | None:
    """Most recent ATS activity we know about: publish or listing update."""
    dates = [d for d in (j.published_at, j.last_listing_update) if d is not None]
    return max(dates) if dates else None


def list_company_jobs_live(
    company: str,
    *,
    posted_since: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Live-fetch a company's job board and return compact listing rows.

    `posted_since` (e.g. '3d', '1w') keeps rows whose freshness — publish
    date or most recent ATS listing update, whichever is later — falls on
    or after the cutoff. Rows with neither date survive the filter (unknown
    is not old). The envelope carries three counts: `total` (full board,
    pre-filter), `matched` (post-filter, pre-slice), and `returned` — so
    `matched > offset + returned` is the unambiguous "there's more" signal
    and `offset` pages through the remainder.

    Raises ValueError for unknown companies, companies with no usable
    board config, or an unparseable `posted_since`.
    """
    # Validate inputs before any I/O — a bad duration must not cost a
    # full board fetch.
    cutoff: date | None = None
    if posted_since:
        from jobbuddy.core.durations import parse_duration_to_date

        cutoff = date.fromisoformat(parse_duration_to_date(posted_since))

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

    with get_fetcher(resolved) as fetcher:
        jobs = fetcher.list_jobs()
    total = len(jobs)

    if cutoff is not None:
        jobs = [
            j for j in jobs
            if (fresh := job_freshness(j)) is None or fresh >= cutoff
        ]
    matched = len(jobs)

    # Newest first by publish date; undated rows land last.
    jobs.sort(
        key=lambda j: (j.published_at is not None, j.published_at or date.min),
        reverse=True,
    )

    rows = []
    for j in jobs[offset:offset + limit]:
        dumped = j.model_dump()
        if dumped.get("team") == dumped.get("department"):
            dumped["team"] = None  # some ATSes mirror the fields; don't pay twice
        rows.append({
            k: dumped[k] for k in _ROW_FIELDS
            if dumped.get(k) not in (None, "")
        })

    envelope = {
        "company": resolved.slug,
        "company_name": resolved.name,
        "ats": resolved.ats,
        "total": total,
        "matched": matched,
        "returned": len(rows),
        "rows": rows,
    }
    if offset:
        envelope["offset"] = offset
    return envelope
