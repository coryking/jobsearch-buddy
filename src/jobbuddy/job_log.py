"""Activity log operations — thin wrappers around JobStore.

All consumers (CLI, MCP) import from here and expect list[dict] back
with keys: date, company, role, job_id, action, person, location, status, url, notes.

Every read/write is scoped to a single account_id — the FK on activity_log
is NOT NULL, and there is no cross-account view at this layer.
"""

from uuid import UUID

from jobbuddy.store import JobStore

FIELDNAMES = ["date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"]


def _store() -> JobStore:
    return JobStore()


def read_log(account_id: UUID) -> list[dict]:
    """Read all rows from one account's activity log."""
    with _store() as s:
        return s.read_activity_log(account_id)


def append_row(
    account_id: UUID,
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
    """Append a row to the activity log under one account. Returns the row dict."""
    with _store() as s:
        return s.append_activity(
            account_id, company, role, action,
            job_id=job_id, person=person, location=location,
            status=status, url=url, notes=notes, row_date=row_date,
        )


def find_duplicates(
    account_id: UUID, url: str = "", company: str = "", role: str = "",
) -> list[dict]:
    """Find one account's rows matching a URL, or company+role combo."""
    with _store() as s:
        return s.find_activity_duplicates(account_id, url=url, company=company, role=role)


def find_by_company(account_id: UUID, company: str) -> list[dict]:
    """Find one account's rows for a given company (case-insensitive)."""
    with _store() as s:
        return s.find_activity_by_company(account_id, company)


def unique_companies(account_id: UUID) -> set[str]:
    """Return deduplicated company names from one account's activity log."""
    with _store() as s:
        return s.unique_activity_companies(account_id)
