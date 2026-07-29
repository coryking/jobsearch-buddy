"""Tests for the stateless live-listing core (`jobbuddy.core.live`).

The live path never touches the jobs table — every row comes straight off
the ATS response. These tests mock the fetcher layer; company resolution
uses the standard test registry from conftest.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.core.live import list_company_jobs_live
from jobbuddy.models import Job


def _job(i: str, title: str, *, published: str | None = None, location: str = "Remote",
         description: str | None = "a very long JD " * 200, salary: str | None = None) -> Job:
    return Job(
        id=i, title=title, location=location,
        url=f"https://example.test/{i}", apply_url=f"https://example.test/{i}/apply",
        published_at=published, salary=salary, description=description,
    )


def _mock_fetcher(jobs: list[Job]) -> MagicMock:
    fetcher = MagicMock()
    fetcher.list_jobs.return_value = jobs
    return fetcher


PATCH_TARGET = "jobbuddy.core.live.get_fetcher"


def _listing(jobs, company="acme", **kwargs):
    with patch(PATCH_TARGET, return_value=_mock_fetcher(jobs)):
        return list_company_jobs_live(company, **kwargs)


def test_rows_are_compact_no_description():
    listing = _listing([_job("1", "Engineer"), _job("2", "PM")])
    assert listing["total"] == 2
    assert listing["returned"] == 2
    for row in listing["rows"]:
        assert "description" not in row
        assert row["title"] in ("Engineer", "PM")
        assert row["url"].startswith("https://example.test/")


def test_company_resolution_by_name():
    """Display names resolve the same as slugs."""
    listing = _listing([_job("1", "Engineer")], company="Acme Corp")
    assert listing["company"] == "acme"


def test_unknown_company_raises():
    with pytest.raises(ValueError, match="Unknown company"):
        list_company_jobs_live("no-such-co")


def test_published_since_filters_but_keeps_undated():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()
    listing = _listing(
        [
            _job("new", "Fresh Role", published=today),
            _job("old", "Stale Role", published=old),
            _job("undated", "Mystery Role", published=None),
        ],
        published_since="1w",
    )
    ids = {r["id"] for r in listing["rows"]}
    # Undated rows survive the filter — absence of a date is "unknown", not "old".
    assert ids == {"new", "undated"}
    assert listing["total"] == 3  # total is pre-filter board size


def test_rows_sorted_newest_first_undated_last():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()
    listing = _listing(
        [
            _job("undated", "Mystery", published=None),
            _job("old", "Old", published=old),
            _job("new", "New", published=today),
        ]
    )
    assert [r["id"] for r in listing["rows"]] == ["new", "old", "undated"]


def test_limit_truncates_and_reports():
    jobs = [_job(str(i), f"Role {i}") for i in range(10)]
    listing = _listing(jobs, limit=3)
    assert listing["total"] == 10
    assert listing["returned"] == 3
    assert len(listing["rows"]) == 3


def test_null_fields_omitted_from_rows():
    assert "salary" not in _listing([_job("1", "Engineer", salary=None)])["rows"][0]
    assert _listing([_job("1", "Engineer", salary="$100K")])["rows"][0]["salary"] == "$100K"


def test_company_without_board_raises():
    """Registered companies with no ATS config can't be live-listed."""
    with pytest.raises(ValueError, match="No job board"):
        list_company_jobs_live("broken-co")
