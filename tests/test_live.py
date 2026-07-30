"""Tests for the stateless live-listing path — core (`jobbuddy.core.live`)
and the MCP tools layered on it (`jobbuddy.mcp_tools.live`).

The live path never touches the jobs table — every row comes straight off
the ATS response. These tests mock the fetcher layer; company resolution
uses the standard test registry from conftest.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.core.live import list_company_jobs_live
from jobbuddy.models import Company, FetchResult, Job


def _job(i: str, title: str, *, published: str | None = None, updated: str | None = None,
         location: str = "Remote", description: str | None = "a very long JD " * 200,
         salary: str | None = None, department: str | None = None,
         team: str | None = None) -> Job:
    return Job(
        id=i, title=title, location=location,
        url=f"https://example.test/{i}", apply_url=f"https://example.test/{i}/apply",
        published_at=published, last_listing_update=updated, salary=salary,
        department=department, team=team, description=description,
    )


def _mock_fetcher(jobs: list[Job]) -> MagicMock:
    fetcher = MagicMock()
    fetcher.list_jobs.return_value = jobs
    # get_fetcher is used as a context manager (guarantees client close)
    fetcher.__enter__.return_value = fetcher
    fetcher.__exit__.return_value = False
    return fetcher


PATCH_TARGET = "jobbuddy.core.live.get_fetcher"


def _listing(jobs, company="acme", **kwargs):
    with patch(PATCH_TARGET, return_value=_mock_fetcher(jobs)):
        return list_company_jobs_live(company, **kwargs)


def test_rows_are_compact_no_description():
    listing = _listing([_job("1", "Engineer"), _job("2", "PM")])
    assert listing["total"] == 2
    assert listing["matched"] == 2
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


def test_posted_since_filters_but_keeps_undated():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()
    listing = _listing(
        [
            _job("new", "Fresh Role", published=today),
            _job("old", "Stale Role", published=old),
            _job("undated", "Mystery Role", published=None),
        ],
        posted_since="1w",
    )
    ids = {r["id"] for r in listing["rows"]}
    # Undated rows survive the filter — absence of a date is "unknown", not "old".
    assert ids == {"new", "undated"}
    assert listing["total"] == 3  # total is pre-filter board size
    assert listing["matched"] == 2


def test_posted_since_honors_listing_update():
    """An evergreen req with an ancient publish date but a recent ATS touch
    counts as fresh — the class of bug issue #53 exists to prevent."""
    ancient = (date.today() - timedelta(days=400)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    listing = _listing(
        [_job("evergreen", "Perennial Role", published=ancient, updated=yesterday)],
        posted_since="1w",
    )
    assert [r["id"] for r in listing["rows"]] == ["evergreen"]
    # And the row carries the signal so the LLM can see why it's "new".
    assert str(listing["rows"][0]["last_listing_update"]) == yesterday


def test_bad_posted_since_rejected_before_fetch():
    """An unparseable window must fail before the board fetch is paid for."""
    fetcher = _mock_fetcher([])
    with patch(PATCH_TARGET, return_value=fetcher):
        with pytest.raises(ValueError, match="Invalid duration"):
            list_company_jobs_live("acme", posted_since="last week")
    fetcher.list_jobs.assert_not_called()


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
    assert listing["matched"] == 10
    assert listing["returned"] == 3
    assert len(listing["rows"]) == 3


def test_offset_pages_through_matches():
    today = date.today()
    jobs = [
        _job(str(i), f"Role {i}", published=(today - timedelta(days=i)).isoformat())
        for i in range(10)
    ]
    page2 = _listing(jobs, limit=3, offset=3)
    assert [r["id"] for r in page2["rows"]] == ["3", "4", "5"]
    assert page2["offset"] == 3
    assert page2["matched"] == 10
    # Past-the-end offset returns an empty page, not an error.
    assert _listing(jobs, limit=3, offset=20)["rows"] == []


def test_null_fields_omitted_from_rows():
    assert "salary" not in _listing([_job("1", "Engineer", salary=None)])["rows"][0]
    assert _listing([_job("1", "Engineer", salary="$100K")])["rows"][0]["salary"] == "$100K"


def test_team_dropped_when_it_mirrors_department():
    row = _listing([_job("1", "Eng", department="Product", team="Product")])["rows"][0]
    assert row["department"] == "Product"
    assert "team" not in row
    row = _listing([_job("1", "Eng", department="Product", team="Growth")])["rows"][0]
    assert row["team"] == "Growth"


def test_company_without_board_raises():
    """Registered companies with no ATS config can't be live-listed."""
    with pytest.raises(ValueError, match="No job board"):
        list_company_jobs_live("broken-co")


def test_company_with_unsupported_ats_raises():
    fax_co = Company(slug="fax", name="Fax Co", ats="fax_machine", board="fax")
    with patch("jobbuddy.core.live.lookup_by_name", return_value=fax_co):
        with pytest.raises(ValueError, match="No job board"):
            list_company_jobs_live("fax")


def test_fetcher_closed_after_listing():
    fetcher = _mock_fetcher([_job("1", "Engineer")])
    with patch(PATCH_TARGET, return_value=fetcher):
        list_company_jobs_live("acme")
    fetcher.__exit__.assert_called_once()


# ---------------------------------------------------------------------------
# MCP tool layer — the envelope/error contract the calling LLM branches on
# ---------------------------------------------------------------------------

ACME = Company(slug="acme", name="Acme Corp", ats="greenhouse", board="acme")


def _fetch_result(job_id: str = "123") -> FetchResult:
    return FetchResult(company=ACME, job=_job(job_id, "Engineer", salary="$100K"))


class TestGetJobTool:
    def test_url_path_returns_compact_json(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.fetch.fetch_from_url", return_value=_fetch_result()) as m:
            result = live_module.get_job(url="https://example.test/123")
        m.assert_called_once_with("https://example.test/123")
        data = json.loads(result)
        assert data["title"] == "Engineer"
        assert data["company"] == "Acme Corp"
        assert data["salary"] == "$100K"

    def test_company_and_id_path(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.fetch_by_id", return_value=_fetch_result()) as m:
            result = live_module.get_job(company="acme", job_id="123")
        m.assert_called_once_with("acme", "123")
        assert json.loads(result)["id"] == "123"

    def test_neither_url_nor_id_is_a_usage_error(self):
        from jobbuddy.mcp_tools import live as live_module

        result = live_module.get_job()
        assert result.startswith("Error:")
        assert "url" in result and "job_id" in result

    def test_value_error_becomes_error_string(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.fetch.fetch_from_url", side_effect=ValueError("Unrecognized ATS URL")):
            result = live_module.get_job(url="https://example.com/nope")
        assert result.startswith("Error:")
        assert "Unrecognized" in result


class TestListCompanyJobsTool:
    def test_value_error_becomes_error_envelope(self):
        from jobbuddy.mcp_tools import live as live_module

        result = live_module.list_company_jobs(company="no-such-co")
        assert set(result) == {"error"}
        assert "Unknown company" in result["error"]

    def test_fetch_failure_becomes_error_envelope(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.list_company_jobs_live", side_effect=RuntimeError("board 404")):
            result = live_module.list_company_jobs(company="acme")
        assert "Live fetch failed for acme" in result["error"]
        assert "board 404" in result["error"]

    def test_passes_params_through(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.list_company_jobs_live", return_value={"rows": []}) as m:
            live_module.list_company_jobs(company="acme", posted_since="1w", limit=10, offset=5)
        m.assert_called_once_with("acme", posted_since="1w", limit=10, offset=5)
