"""Tests for the Ashby fetcher.

Ashby splits a posting's locations across `location` (primary) and
`secondaryLocations` (everything else). A listing shown on the web as
"New York, NY; Austin, TX; Seattle, WA" arrives as location="New York, NY"
plus two secondaryLocations entries — dropping the secondaries silently
excludes the job from location-based reasoning.
"""

from unittest.mock import MagicMock

import httpx

from jobbuddy.fetchers.ashby import AshbyFetcher


SAMPLE_BOARD = {
    "jobs": [
        {
            "id": "aaa-111",
            "title": "Product Manager",
            "location": "New York, NY",
            "secondaryLocations": [
                {"location": "Austin, TX"},
                {"location": "Seattle, WA"},
            ],
            "jobUrl": "https://jobs.ashbyhq.com/acme/aaa-111",
            "applyUrl": "https://jobs.ashbyhq.com/acme/aaa-111/application",
            "publishedAt": "2026-07-24T14:54:20.363+00:00",
            "descriptionPlain": "desc",
        },
        {
            "id": "bbb-222",
            "title": "Engineer",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/acme/bbb-222",
            "applyUrl": "https://jobs.ashbyhq.com/acme/bbb-222/application",
            "publishedAt": "2026-07-01T00:00:00Z",
            "descriptionPlain": "desc",
        },
    ]
}


def _make_fetcher() -> AshbyFetcher:
    f = AshbyFetcher("acme")
    f.client = MagicMock()
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = SAMPLE_BOARD
    resp.raise_for_status = MagicMock()
    f.client.get.return_value = resp
    return f


def test_secondary_locations_joined_into_location():
    jobs = _make_fetcher().list_jobs()
    pm = next(j for j in jobs if j.id == "aaa-111")
    assert pm.location == "New York, NY; Austin, TX; Seattle, WA"


def test_no_secondary_locations_is_primary_only():
    jobs = _make_fetcher().list_jobs()
    eng = next(j for j in jobs if j.id == "bbb-222")
    assert eng.location == "Remote"
