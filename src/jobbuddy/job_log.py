"""CSV operations for data/job-search-log.csv."""

import csv
from datetime import date
from pathlib import Path

def _log_path() -> Path:
    from jobbuddy.settings import get_settings
    return get_settings().data_dir / "job-search-log.csv"

FIELDNAMES = ["date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"]


def ensure_log_exists() -> None:
    """Create the CSV with headers if it doesn't exist."""
    log_path = _log_path()
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def read_log() -> list[dict]:
    """Read all rows from the job search log."""
    ensure_log_exists()
    with open(_log_path(), newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


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
    """Append a row to the job search log. Returns the row dict."""
    ensure_log_exists()
    row = {
        "date": row_date or date.today().isoformat(),
        "company": company,
        "role": role,
        "job_id": job_id,
        "action": action,
        "person": person,
        "location": location,
        "status": status,
        "url": url,
        "notes": notes,
    }
    with open(_log_path(), "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)
    return row


def find_duplicates(url: str = "", company: str = "", role: str = "") -> list[dict]:
    """Find rows matching a URL, or company+role combo."""
    rows = read_log()
    matches = []
    for row in rows:
        if url and row.get("url", "").strip() == url.strip():
            matches.append(row)
        elif company and role and row.get("company", "").lower() == company.lower() and row.get("role", "").lower() == role.lower():
            matches.append(row)
    return matches


def find_by_company(company: str) -> list[dict]:
    """Find all rows for a given company (case-insensitive)."""
    rows = read_log()
    return [r for r in rows if r.get("company", "").lower() == company.lower()]


def unique_companies() -> set[str]:
    """Return deduplicated company names from the CSV log."""
    rows = read_log()
    return {r["company"] for r in rows if r.get("company")}
