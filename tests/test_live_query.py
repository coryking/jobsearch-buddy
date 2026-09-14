"""Tests for the `query` parameter on list_company_jobs_live.

Covers three layers:
1. Client-side query filtering logic — unit tests on Job lists.
2. list_company_jobs_live with query — envelope fields (query_mode),
   server vs client-side filtering, interaction with posted_since.
3. A real fetcher (Rippling) passing query through to the ATS.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.core.live import list_company_jobs_live
from jobbuddy.models import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job(
    i: str,
    title: str,
    *,
    location: str = "Remote",
    department: str | None = None,
    published: str | None = None,
) -> Job:
    return Job(
        id=i,
        title=title,
        location=location,
        url=f"https://example.test/{i}",
        apply_url=f"https://example.test/{i}/apply",
        published_at=published,
        department=department,
        description="placeholder description",
    )


SAMPLE_JOBS = [
    _job("1", "Senior Product Manager", location="Seattle, WA", department="Product"),
    _job("2", "Infrastructure Engineer", location="Remote", department="Engineering"),
    _job("3", "Product Designer", location="New York, NY", department="Design"),
    _job("4", "Data Scientist", location="Seattle, WA", department="Data"),
    _job("5", "Sales Manager", location="Chicago, IL", department="Sales"),
]


def _mock_fetcher(jobs: list[Job], *, supports_query: bool = False):
    fetcher = MagicMock()
    fetcher.list_jobs.return_value = jobs
    fetcher.supports_query = supports_query
    # Support type(fetcher).supports_query access pattern
    type(fetcher).supports_query = supports_query
    fetcher.__enter__ = MagicMock(return_value=fetcher)
    fetcher.__exit__ = MagicMock(return_value=False)
    return fetcher


PATCH_TARGET = "jobbuddy.core.live.get_fetcher"


def _listing(jobs, company="acme", supports_query=False, **kwargs):
    fetcher = _mock_fetcher(jobs, supports_query=supports_query)
    with patch(PATCH_TARGET, return_value=fetcher):
        result = list_company_jobs_live(company, **kwargs)
    return result, fetcher


# ---------------------------------------------------------------------------
# 1. Client-side query filtering
# ---------------------------------------------------------------------------

class TestClientSideQueryFiltering:
    """Test the filtering logic applied when the ATS doesn't support native search."""

    def test_single_term_matches_title(self):
        result, _ = _listing(SAMPLE_JOBS, query="product")
        titles = {r["title"] for r in result["rows"]}
        assert "Senior Product Manager" in titles
        assert "Product Designer" in titles
        assert "Infrastructure Engineer" not in titles

    def test_single_term_matches_location(self):
        result, _ = _listing(SAMPLE_JOBS, query="seattle")
        ids = {r["id"] for r in result["rows"]}
        assert "1" in ids  # Seattle, WA
        assert "4" in ids  # Seattle, WA
        assert "2" not in ids  # Remote

    def test_single_term_matches_department(self):
        result, _ = _listing(SAMPLE_JOBS, query="engineering")
        ids = {r["id"] for r in result["rows"]}
        assert "2" in ids
        assert "1" not in ids

    def test_multi_word_and_semantics(self):
        """All whitespace-separated terms must match (AND logic)."""
        result, _ = _listing(SAMPLE_JOBS, query="product seattle")
        # Only "Senior Product Manager" matches both "product" AND "seattle"
        ids = {r["id"] for r in result["rows"]}
        assert ids == {"1"}

    def test_case_insensitive(self):
        result, _ = _listing(SAMPLE_JOBS, query="PRODUCT")
        titles = {r["title"] for r in result["rows"]}
        assert "Senior Product Manager" in titles
        assert "Product Designer" in titles

    def test_empty_query_returns_all(self):
        result, _ = _listing(SAMPLE_JOBS, query="")
        assert result["matched"] == len(SAMPLE_JOBS)

    def test_no_match_returns_empty(self):
        result, _ = _listing(SAMPLE_JOBS, query="blockchain")
        assert result["matched"] == 0
        assert result["rows"] == []


# ---------------------------------------------------------------------------
# 2. list_company_jobs_live envelope behavior
# ---------------------------------------------------------------------------

class TestQueryEnvelope:
    """Verify query_mode in the envelope and server vs client-side filtering."""

    def test_server_mode_when_fetcher_supports_query(self):
        result, fetcher = _listing(
            SAMPLE_JOBS, supports_query=True, query="product",
        )
        assert result["query_mode"] == "server"
        # Query was passed to fetcher
        fetcher.list_jobs.assert_called_once_with(query="product")
        # No client-side filtering applied — the fetcher's result is trusted
        assert result["matched"] == len(SAMPLE_JOBS)

    def test_client_mode_when_fetcher_does_not_support_query(self):
        result, _ = _listing(SAMPLE_JOBS, query="product")
        assert result["query_mode"] == "client"
        # Client-side filtering was applied
        assert result["matched"] < len(SAMPLE_JOBS)

    def test_no_query_mode_when_query_empty(self):
        result, _ = _listing(SAMPLE_JOBS, query="")
        assert "query_mode" not in result

    def test_no_query_mode_when_query_not_passed(self):
        result, _ = _listing(SAMPLE_JOBS)
        assert "query_mode" not in result

    def test_total_reflects_full_board_regardless_of_query(self):
        result, _ = _listing(SAMPLE_JOBS, query="product")
        assert result["total"] == len(SAMPLE_JOBS)

    def test_matched_reflects_post_query_filtering(self):
        result, _ = _listing(SAMPLE_JOBS, query="product")
        # "product" matches 2: Senior Product Manager + Product Designer
        assert result["matched"] == 2

    def test_query_and_posted_since_narrow_together(self):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=60)).isoformat()
        jobs = [
            _job("1", "Product Manager", published=today),
            _job("2", "Product Designer", published=old),
            _job("3", "Engineer", published=today),
        ]
        result, _ = _listing(jobs, query="product", posted_since="1w")
        ids = {r["id"] for r in result["rows"]}
        # Only "Product Manager" matches both query and recency
        assert ids == {"1"}
        assert result["total"] == 3

    def test_query_passed_to_fetcher_when_supported(self):
        """Verify the query kwarg actually reaches list_jobs()."""
        fetcher = _mock_fetcher(SAMPLE_JOBS, supports_query=True)
        with patch(PATCH_TARGET, return_value=fetcher):
            list_company_jobs_live("acme", query="infrastructure")
        fetcher.list_jobs.assert_called_once_with(query="infrastructure")


# ---------------------------------------------------------------------------
# 3. Real fetcher: Rippling passes query as searchTerm
# ---------------------------------------------------------------------------

class TestRipplingQueryPassthrough:
    """Verify Rippling ships query to the ATS as a searchTerm param."""

    def test_rippling_has_supports_query_true(self):
        from jobbuddy.fetchers.rippling import RipplingFetcher
        assert RipplingFetcher.supports_query is True

    def test_rippling_passes_search_term(self):
        from jobbuddy.fetchers.rippling import RipplingFetcher

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        fetcher = RipplingFetcher(board="test-co")
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        fetcher.list_jobs(query="product manager")

        fetcher.client.get.assert_called_once()
        call_args = fetcher.client.get.call_args
        url = call_args[0][0]
        assert "board/test-co/jobs" in url
        params = call_args[1].get("params", {})
        assert params.get("searchTerm") == "product manager"

    def test_rippling_no_search_term_without_query(self):
        from jobbuddy.fetchers.rippling import RipplingFetcher

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        fetcher = RipplingFetcher(board="test-co")
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        fetcher.list_jobs()

        call_args = fetcher.client.get.call_args
        # No params at all, or no searchTerm param
        params = call_args[1].get("params") if call_args[1] else None
        assert params is None or "searchTerm" not in params


# ---------------------------------------------------------------------------
# 4. MCP tool passes query through
# ---------------------------------------------------------------------------

class TestMCPToolQuery:
    def test_mcp_passes_query_to_core(self):
        from jobbuddy.mcp_tools import live as live_module

        with patch("jobbuddy.core.list_company_jobs_live", return_value={"rows": []}) as m:
            live_module.list_company_jobs(company="acme", query="product manager")
        m.assert_called_once_with(
            "acme", posted_since="", limit=50, offset=0, query="product manager",
        )
