"""Human-friendly duration parsing for `posted_since` filters."""

import re
from datetime import date, timedelta


def parse_duration_to_date(value: str) -> str:
    """Parse a human-friendly duration into an ISO date string (YYYY-MM-DD).

    Accepts: 24h, 3d, 1w, 2w, etc.
    Raises ValueError on invalid input.
    """
    m = re.fullmatch(r"(\d+)\s*(h|d|w)", value.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration '{value}'. Use e.g. 24h, 3d, 1w, 2w.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "h":
        delta = timedelta(hours=n)
    elif unit == "d":
        delta = timedelta(days=n)
    else:
        delta = timedelta(weeks=n)
    return (date.today() - delta).isoformat()
