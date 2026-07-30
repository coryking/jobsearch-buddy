"""Shared core logic for ATS operations — used by both CLI and MCP server.

This package raises ValueError on errors instead of SystemExit so callers can
handle them. No Rich/Typer/FastMCP dependencies.

The historical `jobbuddy.core` module was a single 600-line file. It is now a
package split along seams; the public surface is preserved here so callers
that import `from jobbuddy.core import X` keep working.
"""

from jobbuddy.core.companies import (
    find_companies,
    resolve_company_slugs,
    resolve_exclude_companies,
)
from jobbuddy.core.durations import parse_duration_to_date
from jobbuddy.core.fetch import (
    SUPPORTED_DOMAINS,
    fetch_by_id,
    fetch_from_url,
    is_supported_ats_url,
)
from jobbuddy.core.live import list_company_jobs_live
from jobbuddy.core.output import (
    job_to_markdown,
    result_to_dict,
    save_job_listing,
)
from jobbuddy.core.search import (
    merge_watchlist_defaults,
    search_jobs,
    survey_jobs_by_companies,
)
from jobbuddy.core.types import CompanyEnvelope, JobRow

__all__ = [
    "CompanyEnvelope",
    "JobRow",
    "SUPPORTED_DOMAINS",
    "fetch_by_id",
    "fetch_from_url",
    "find_companies",
    "is_supported_ats_url",
    "job_to_markdown",
    "list_company_jobs_live",
    "merge_watchlist_defaults",
    "parse_duration_to_date",
    "resolve_company_slugs",
    "resolve_exclude_companies",
    "result_to_dict",
    "save_job_listing",
    "search_jobs",
    "survey_jobs_by_companies",
]
