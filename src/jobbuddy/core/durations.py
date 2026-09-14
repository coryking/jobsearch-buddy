"""Human-friendly duration parsing for `posted_since` filters."""

import re
from datetime import date, timedelta


def parse_duration_to_date(value: str) -> str:
    """Parse a human-friendly duration into an ISO date string (YYYY-MM-DD).

    Accepts: 24h, 3d, 1w, 6m, 1y, or a YYYY-MM-DD date (passed through).
    Raises ValueError on invalid input.
    """
    stripped = value.strip()

    # ISO date passthrough: YYYY-MM-DD
    iso_match = re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped)
    if iso_match:
        # Validate the date is real (rejects 2026-13-01 etc.)
        return date.fromisoformat(stripped).isoformat()

    m = re.fullmatch(r"(\d+)\s*(h|d|w|m|y)", stripped.lower())
    if not m:
        raise ValueError(
            f"Invalid duration '{value}'. Use e.g. 24h, 3d, 1w, 6m, 1y, or YYYY-MM-DD."
        )
    n, unit = int(m.group(1)), m.group(2)
    if unit == "h":
        delta = timedelta(hours=n)
        return (date.today() - delta).isoformat()
    elif unit == "d":
        delta = timedelta(days=n)
        return (date.today() - delta).isoformat()
    elif unit == "w":
        delta = timedelta(weeks=n)
        return (date.today() - delta).isoformat()
    else:
        # m or y — calendar math via relativedelta
        from dateutil.relativedelta import relativedelta

        if unit == "m":
            return (date.today() - relativedelta(months=n)).isoformat()
        else:
            return (date.today() - relativedelta(years=n)).isoformat()
