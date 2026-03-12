"""Activity log operations — thin wrappers around JobStore.

All consumers (CLI, MCP) import from here and expect list[dict] back
with keys: date, company, role, job_id, action, person, location, status, url, notes.
"""

from jobbuddy.store import JobStore

FIELDNAMES = ["date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"]


def _store() -> JobStore:
    return JobStore()


def read_log() -> list[dict]:
    """Read all rows from the activity log."""
    with _store() as s:
        return s.read_activity_log()


def append_row(
    company: str,
    role: str,
    action: str,
    *,
    job_id: str = "",
    person: str = "",
    location: str = "",
    status: str = "",
    url: str = "",
    notes: str = "",
    row_date: str | None = None,
) -> dict:
    """Append a row to the activity log. Returns the row dict."""
    with _store() as s:
        return s.append_activity(
            company, role, action,
            job_id=job_id, person=person, location=location,
            status=status, url=url, notes=notes, row_date=row_date,
        )


def find_duplicates(url: str = "", company: str = "", role: str = "") -> list[dict]:
    """Find rows matching a URL, or company+role combo."""
    with _store() as s:
        return s.find_activity_duplicates(url=url, company=company, role=role)


def find_by_company(company: str) -> list[dict]:
    """Find all rows for a given company (case-insensitive)."""
    with _store() as s:
        return s.find_activity_by_company(company)


def unique_companies() -> set[str]:
    """Return deduplicated company names from the activity log."""
    with _store() as s:
        return s.unique_activity_companies()
