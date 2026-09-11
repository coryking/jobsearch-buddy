"""Pydantic row/envelope types returned by the search functions.

Both CLI and MCP layers serialize these directly. Field semantics live on the
class docstrings — keep those accurate; the MCP wrapper does not re-document
them.
"""

from pydantic import BaseModel, Field


class JobRow(BaseModel):
    """One ranked job row returned by search_jobs / survey_jobs_by_companies.

    `posted` is an ISO date string when known; `snippet` is a ts_headline
    excerpt (when query is set) or a passive prefix of `short_jd` (when not).
    `updated` is an ATS-side freshness signal; when both are present, prefer
    `updated` over `posted` to judge whether a listing is current.
    """

    job_id: str
    title: str
    location: str | None = None
    posted: str | None = None
    updated: str | None = Field(
        default=None,
        description=(
            "ATS-side last-touch date (ISO date string). Populated by "
            "Greenhouse, Amazon, Eightfold v2, Jibe, and Avature; None for "
            "other platforms. When `updated` and `posted` both appear and "
            "diverge, `posted` is the original publish date and `updated` is "
            "the most recent ATS-side edit — the listing is old but actively "
            "republished. Use `updated` (not `posted`) to judge freshness "
            "when both are present."
        ),
    )
    snippet: str | None = None
    url: str | None = None
    salary: str | None = None
    company_slug: str
    company_name: str | None = None
    # Populated by MCP wrappers from the per-account activity log; None when
    # the user has not logged anything against this row.
    applied: str | None = None


class CompanyEnvelope(BaseModel):
    """Per-company match envelope for survey_jobs_by_companies.

    `matches` is the count under the active filter set. `matches_without_date`
    is populated only when `posted_since` was set (lets the caller see how
    many candidates the date filter dropped). `top` is the top-N rows.
    """

    matches: int
    matches_without_date: int | None = None
    top: list[JobRow]


def to_jobrow(row: dict) -> JobRow:
    """Convert a store row to a typed JobRow.

    Accepts both raw rows from `search_jobs_fts` (carrying `published_at`
    and `short_jd`) and pre-normalized rows from `survey_jobs_by_company`
    (carrying `posted` and `snippet`). Falls back to the raw fields when
    the normalized ones are absent so the flat path also gets a passive
    snippet (first ~25 words of `short_jd`).
    """
    posted = row.get("posted")
    if posted is None:
        pa = row.get("published_at")
        posted = pa.isoformat() if hasattr(pa, "isoformat") else (pa or None)

    updated = row.get("updated")
    if updated is None:
        llu = row.get("last_listing_update")
        updated = llu.isoformat() if hasattr(llu, "isoformat") else (llu or None)

    snippet = row.get("snippet")
    if snippet is None:
        short_jd = row.get("short_jd")
        if short_jd:
            words = short_jd.split()
            if words:
                snippet = " ".join(words[:25])

    return JobRow(
        job_id=row["job_id"],
        title=row["title"],
        location=row.get("location") or None,
        posted=posted,
        updated=updated,
        snippet=snippet,
        url=row.get("url") or None,
        salary=row.get("salary"),
        company_slug=row["company_slug"],
        company_name=row.get("company_name") or row["company_slug"],
    )
