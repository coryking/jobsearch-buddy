"""Tests for the Greenhouse fetcher.

Greenhouse exposes both `first_published` and `updated_at` on every job in
the board API. We use `first_published` for `published_at` (publish-date
semantics) and `updated_at` for `last_listing_update` (freshness signal).
See issue #53 for the live-probe analysis that motivated splitting these
into two columns.
"""

from datetime import date
from unittest.mock import MagicMock

import httpx

from jobbuddy.fetchers.greenhouse import GreenhouseFetcher


SAMPLE_BOARD = {
    "jobs": [
        {
            "id": 159214,
            "title": "Sales Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/159214",
            "location": {"name": "Remote"},
            "departments": [{"name": "Sales"}],
            "first_published": "2019-01-07T22:26:28-05:00",
            "updated_at": "2026-05-06T16:25:59-04:00",
            "content": "<p>desc</p>",
        },
        {
            "id": 200000,
            "title": "Greenfield Role",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/200000",
            "location": {"name": "NYC"},
            "departments": [],
            # Older payload shape: no updated_at field at all.
            "first_published": "2026-04-01T00:00:00Z",
            "content": "",
        },
    ]
}


def _make_fetcher() -> GreenhouseFetcher:
    f = GreenhouseFetcher("acme")
    f.client = MagicMock()
    return f


def _mock_response(json_data) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def test_published_at_uses_first_published():
    """Pure-insert date column stays on first_published — backward-compatible."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response(SAMPLE_BOARD)
    jobs = f.list_jobs()
    assert jobs[0].published_at == date(2019, 1, 7)


def test_last_listing_update_uses_updated_at():
    f = _make_fetcher()
    f.client.get.return_value = _mock_response(SAMPLE_BOARD)
    jobs = f.list_jobs()
    assert jobs[0].last_listing_update == date(2026, 5, 6)


def test_missing_updated_at_leaves_last_listing_update_null():
    f = _make_fetcher()
    f.client.get.return_value = _mock_response(SAMPLE_BOARD)
    jobs = f.list_jobs()
    assert jobs[1].last_listing_update is None
    assert jobs[1].published_at == date(2026, 4, 1)
