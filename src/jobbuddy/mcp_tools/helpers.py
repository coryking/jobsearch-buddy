"""Cross-tool helpers for the MCP layer.

Tool modules pull `decorate_applied`, `compact_json`, and the shared field
description constants from here so the wire format stays uniform across
search and survey paths.
"""

import json

from jobbuddy.core import JobRow
from jobbuddy.job_log import read_log

VALID_ACTIONS = {"Application", "Contact", "Screen", "Interview", "Referral", "Reach-out", "Inquery"}

QUERY_FIELD_DESC = (
    "What the user is looking for, expressed in Postgres "
    "`websearch_to_tsquery('english', ...)` syntax. Bare whitespace-separated "
    "terms are AND ('rust backend'); lowercase `or` between terms is OR "
    "('python or go'); `-term` excludes ('engineer -manager'); double-quoted "
    "phrases match consecutive tokens ('\"staff engineer\"'). The English "
    "stemmer is applied, so 'engineering' matches 'engineer'. Pass concrete "
    "substance words like 'kafka' or 'paint booth' that would actually "
    "appear in a posting. For vibe / kind-of-company queries, use "
    "`find_companies` first and pass the slugs to `survey_jobs_by_companies`. "
    "Leave empty to browse by posted_since / location alone."
)

WATCHLIST_FILTER_DESC = (
    "Saved-search defaults applied when search_jobs is called with "
    "watchlist=<slug>. JSON object with optional keys matching search_jobs "
    "params: `query`, `location_filter`, `posted_since`, "
    "`exclude_companies`. Keys not set fall through to caller args. "
    "Example: {\"query\": \"staff engineer\", \"posted_since\": \"7d\"}."
)


def compact_json(d: dict) -> str:
    """JSON-serialize a dict, stripping empty/None values and using no indent."""
    return json.dumps({k: v for k, v in d.items() if v is not None and v != ""})


def decorate_applied(rows: list[JobRow], account_id) -> None:
    """Set `applied` on each JobRow that the account has logged activity against.

    Matches first by `job_id` (the precise hit) and falls back to
    (company_name, role-title) for log entries written before a job_id was
    captured. Mutates the rows in place.
    """
    if not rows:
        return
    log_entries = read_log(account_id)
    if not log_entries:
        return
    by_job_id: dict[str, list[dict]] = {}
    by_company: dict[str, list[dict]] = {}
    for entry in log_entries:
        if entry.get("job_id"):
            by_job_id.setdefault(entry["job_id"], []).append(entry)
        co = (entry.get("company") or "").lower()
        if co:
            by_company.setdefault(co, []).append(entry)
    for row in rows:
        matched = by_job_id.get(row.job_id, [])
        if not matched and row.company_name:
            co_entries = by_company.get(row.company_name.lower(), [])
            matched = [
                e for e in co_entries
                if (e.get("role") or "").lower() == row.title.lower()
            ]
        if matched:
            row.applied = ", ".join(
                f"{m.get('date', '')} {m.get('action', '')}".strip()
                for m in matched
            )
