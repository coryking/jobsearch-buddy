"""Stateless live-fetch tools — the primary job-lookup surface.

Every tool here hits the ATS directly at call time. Nothing is read from
or written to the jobs corpus; what the caller sees is what the job board
says right now. `get_job` fetches one posting (by URL or company+id),
`list_company_jobs` fetches a whole board, `get_application_form` fetches
the questions behind the Apply button.
"""

import json
import logging
from typing import Annotated

from pydantic import Field

from jobbuddy.mcp_tools.app import mcp
from jobbuddy.models import CompactJob

log = logging.getLogger(__name__)


# Not readOnlyHint: the URL path auto-registers unknown companies (a registry
# insert). Idempotent though — re-registering the same company is a no-op.
@mcp.tool(annotations={
    "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True,
})
def get_job(
    url: Annotated[str, Field(description=(
        "Job posting URL — anything the user pasted (LinkedIn deep-link, "
        "careers-page link, board URL). The ATS is auto-detected; unknown "
        "companies are registered on the fly."
    ))] = "",
    company: Annotated[str, Field(description=(
        "Company slug or name, when addressing by company+job_id instead "
        "of URL (e.g. an id from a list_company_jobs row)."
    ))] = "",
    job_id: Annotated[str, Field(description=(
        "The ATS job id, paired with `company`."
    ))] = "",
) -> str:
    """Fetch one job posting live from its ATS and return normalized JSON —
    title, location(s), salary, department, publish date, full description,
    apply URL.

    Use whenever the user pastes a job URL or asks about a specific posting
    ("what's this role?", "fetch the JD for me"). The data is fetched at
    call time, so it reflects what the job board says right now — including
    whether the posting still exists. Address by `url` (preferred) or by
    `company` + `job_id`. Read-only; use log_job_application to record an
    application."""
    from jobbuddy.core import fetch_by_id
    from jobbuddy.core.fetch import fetch_from_url

    try:
        if url:
            result = fetch_from_url(url)
        elif company and job_id:
            result = fetch_by_id(company, job_id)
        else:
            return "Error: pass either `url`, or `company` and `job_id`."
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        log.exception("get_job failed (url=%r company=%r job_id=%r)", url, company, job_id)
        return f"Error fetching job: {e}"

    return json.dumps(CompactJob.from_result(result).model_dump(), indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def list_company_jobs(
    company: Annotated[str, Field(description=(
        "Company slug or display name from the registry (see the "
        "ats://companies resource for what's registered)."
    ))],
    posted_since: Annotated[str, Field(description=(
        "Only rows with ATS activity in this window (e.g. '24h', '3d', "
        "'1w') — publish date OR most recent listing update, whichever is "
        "later, so evergreen postings refreshed recently still count as "
        "fresh. Rows with no date at all are kept — unknown is not old."
    ))] = "",
    limit: Annotated[int, Field(ge=1, le=1000, description=(
        "Max rows returned. `matched > offset + returned` in the envelope "
        "means there are more matching rows — page with `offset` or "
        "narrow with `posted_since`."
    ))] = 50,
    offset: Annotated[int, Field(ge=0, description=(
        "Skip this many matching rows (newest-first order) — for paging "
        "past `limit` on large boards."
    ))] = 0,
) -> dict:
    """List a company's open jobs live from its ATS — compact rows (title,
    location, salary, published date, id, url), newest first.

    Use for "what's open at X?", "anything new at X this week?"
    (posted_since='1w'), or scanning the user's companies of interest —
    one call per company. Most boards answer in one round trip; large
    paginated ATSes (Workday especially) can take tens of seconds per
    company, so fan those out sparingly rather than all at once. The board
    is fetched at call time: no staleness, and an error means the fetch
    failed rather than silently serving old rows.

    The envelope reports `total` (whole board), `matched` (after
    posted_since), and `returned` (this page). Rows carry enough to rank
    and filter in-context — do that yourself rather than re-calling.
    Descriptions are deliberately excluded; call `get_job` for the handful
    of rows the user cares about."""
    from jobbuddy.core import list_company_jobs_live

    try:
        return list_company_jobs_live(
            company, posted_since=posted_since, limit=limit, offset=offset,
        )
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        log.exception("list_company_jobs failed for %r", company)
        return {"error": f"Live fetch failed for {company}: {e}"}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def get_application_form(
    url: Annotated[str, Field(description=(
        "Job posting URL — the same URL surfaced by get_job / "
        "list_company_jobs rows."
    ))],
) -> str:
    """Fetch the application form (questions an applicant must answer) for one
    posting, when you want to warn the user about surprise questions before
    they click Apply ("this one wants a 1000-word essay", "this one asks for
    references up-front").

    Returns the raw vendor payload as JSON — Greenhouse, Ashby, and Rippling
    are supported. Other ATSes return a short "unsupported" message; the
    user will need to open Apply on the posting to see the form. Read-only;
    does not submit anything."""
    from jobbuddy.fetchers import create_fetcher
    from jobbuddy.fetchers.base import ApplicationFormNotSupported
    from jobbuddy.url import parse_url

    parsed = parse_url(url)
    if parsed is None:
        return (
            f"Could not recognize this URL as a supported job-board posting: {url}. "
            "Pass the canonical posting URL surfaced by get_job or "
            "list_company_jobs."
        )

    try:
        fetcher = create_fetcher(parsed.ats, board=parsed.board)
    except ValueError as e:
        return f"Error: {e}"

    try:
        payload = fetcher.fetch_application_form(parsed.board, parsed.job_id)
    except ApplicationFormNotSupported:
        return (
            f"This ATS ({parsed.ats}) does not expose its application form "
            f"anonymously. The user will need to click 'Apply' on {url} to "
            "see the form."
        )
    except Exception as e:
        log.exception("get_application_form failed for %s", url)
        return f"Error fetching application form for {url}: {e}"

    return json.dumps(payload, indent=2, default=str)
