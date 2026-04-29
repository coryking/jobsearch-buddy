"""Tests for Apple careers fetcher and URL parser."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from jobbuddy.fetchers.apple import AppleFetcher, parse_ssr_html, build_location
from jobbuddy.url import ParsedURL, parse_url


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_SEARCH_RESULT = {
    "positionId": "200660378-0157",
    "postingTitle": "Manufacturing Design Program Manager (MDPM)",
    "transformedPostingTitle": "manufacturing-design-program-manager-mdpm",
    "jobSummary": "Imagine what you could do here. At Apple...",
    "locations": [
        {
            "postLocationId": "postLocation-AST",
            "city": "Austin",
            "stateProvince": "Texas",
            "countryName": "United States",
            "name": "Austin",
            "level": 2,
        }
    ],
    "team": {
        "teamName": "Operations and Supply Chain",
        "teamID": "teamsAndSubTeams-OPMFG",
        "teamCode": "OPMFG",
    },
    "postDateInGMT": "2026-04-20T08:00:00.000Z",
    "homeOffice": False,
}

SAMPLE_SEARCH_RESULT_MULTI_LOCATION = {
    "positionId": "200700001",
    "postingTitle": "Software Engineer",
    "transformedPostingTitle": "software-engineer",
    "jobSummary": "Join the team...",
    "locations": [
        {
            "postLocationId": "postLocation-CUP",
            "city": "Cupertino",
            "stateProvince": "California",
            "countryName": "United States",
            "name": "Cupertino",
            "level": 2,
        },
        {
            "postLocationId": "postLocation-SFO",
            "city": "San Francisco",
            "stateProvince": "California",
            "countryName": "United States",
            "name": "San Francisco",
            "level": 2,
        },
    ],
    "team": {"teamName": "Software Engineering", "teamID": "t-SWE", "teamCode": "SWE"},
    "postDateInGMT": "2026-04-15T12:00:00.000Z",
    "homeOffice": False,
}

SAMPLE_SEARCH_RESULT_COUNTRY_ONLY = {
    "positionId": "200700002",
    "postingTitle": "Remote Analyst",
    "transformedPostingTitle": "remote-analyst",
    "jobSummary": "Work from anywhere...",
    "locations": [
        {
            "postLocationId": "postLocation-US",
            "city": "",
            "stateProvince": "",
            "countryName": "United States",
            "name": "United States",
            "level": 1,
        },
    ],
    "team": {"teamName": "Data", "teamID": "t-DATA", "teamCode": "DATA"},
    "postDateInGMT": "2026-04-10T00:00:00.000Z",
    "homeOffice": True,
}

SAMPLE_JOB_DETAIL = {
    "positionId": "200660378",
    "jobNumber": "200660378-0157",
    "postingTitle": "Manufacturing Design Program Manager (MDPM)",
    "transformedPostingTitle": "manufacturing-design-program-manager-mdpm",
    "jobSummary": "Imagine what you could do here. At Apple...",
    "description": "<p>This is the <b>full job description</b> with HTML.</p>",
    "locations": [
        {
            "city": "Austin",
            "stateProvince": "Texas",
            "countryName": "United States",
            "name": "Austin",
            "level": 2,
        }
    ],
    "teamNames": ["Operations and Supply Chain"],
    "postDateInGMT": "2026-04-20T08:00:00.000Z",
}


def make_ssr_html(search_results: list[dict], total_records: int) -> str:
    """Build fake Apple SSR HTML with hydration data."""
    loader_data = {
        "search": {
            "searchResults": search_results,
            "totalRecords": total_records,
        }
    }
    hydration = {
        "loaderData": {"search": loader_data["search"]},
    }
    # Double-encode: JSON.parse("...") in the page
    inner_json = json.dumps(hydration)
    # Escape for embedding inside a JS string literal
    escaped = inner_json.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
    <html><head><script>
    window.__staticRouterHydrationData = JSON.parse("{escaped}");
    </script></head><body></body></html>
    """


# ---------------------------------------------------------------------------
# SSR parsing tests
# ---------------------------------------------------------------------------


class TestSSRParsing:
    def test_parse_ssr_html_single_result(self):
        html = make_ssr_html([SAMPLE_SEARCH_RESULT], 1)
        results, total = parse_ssr_html(html)
        assert total == 1
        assert len(results) == 1
        assert results[0]["positionId"] == "200660378-0157"

    def test_parse_ssr_html_multiple_results(self):
        html = make_ssr_html(
            [SAMPLE_SEARCH_RESULT, SAMPLE_SEARCH_RESULT_MULTI_LOCATION], 2
        )
        results, total = parse_ssr_html(html)
        assert total == 2
        assert len(results) == 2

    def test_parse_ssr_html_no_match_raises(self):
        with pytest.raises(ValueError, match="hydration"):
            parse_ssr_html("<html><body>no data</body></html>")


# ---------------------------------------------------------------------------
# Location building tests
# ---------------------------------------------------------------------------


class TestBuildLocation:
    def test_city_state_country(self):
        loc = SAMPLE_SEARCH_RESULT["locations"][0]
        assert build_location(loc) == "Austin, Texas, United States"

    def test_country_only(self):
        loc = SAMPLE_SEARCH_RESULT_COUNTRY_ONLY["locations"][0]
        assert build_location(loc) == "United States"

    def test_multiple_locations_joined(self):
        locs = SAMPLE_SEARCH_RESULT_MULTI_LOCATION["locations"]
        result = " | ".join(build_location(loc) for loc in locs)
        assert "Cupertino" in result
        assert "San Francisco" in result


# ---------------------------------------------------------------------------
# list_jobs tests
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_single_page(self):
        fetcher = AppleFetcher("apple")
        html = make_ssr_html([SAMPLE_SEARCH_RESULT], 1)
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        jobs = fetcher.list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "200660378-0157"
        assert job.title == "Manufacturing Design Program Manager (MDPM)"
        assert "Austin" in job.location
        assert job.url == "https://jobs.apple.com/en-us/details/200660378-0157/manufacturing-design-program-manager-mdpm"
        assert job.department == "Operations and Supply Chain"
        assert job.description == "Imagine what you could do here. At Apple..."
        assert str(job.published_at) == "2026-04-20"

    def test_pagination(self):
        """Fetches multiple pages when totalRecords > 20."""
        fetcher = AppleFetcher("apple")

        # Page 1: 20 results, total 25
        page1_results = [
            {**SAMPLE_SEARCH_RESULT, "positionId": f"id-{i}", "transformedPostingTitle": f"job-{i}"}
            for i in range(20)
        ]
        page1_html = make_ssr_html(page1_results, 25)

        # Page 2: 5 results
        page2_results = [
            {**SAMPLE_SEARCH_RESULT, "positionId": f"id-{i}", "transformedPostingTitle": f"job-{i}"}
            for i in range(20, 25)
        ]
        page2_html = make_ssr_html(page2_results, 25)

        responses = []
        for html in [page1_html, page2_html]:
            resp = MagicMock()
            resp.text = html
            resp.raise_for_status = MagicMock()
            responses.append(resp)

        fetcher.client = MagicMock()
        fetcher.client.get.side_effect = responses

        progress_calls = []
        jobs = fetcher.list_jobs(on_progress=lambda f, t: progress_calls.append((f, t)))
        assert len(jobs) == 25
        assert progress_calls[-1] == (25, 25)

    def test_progress_callback(self):
        fetcher = AppleFetcher("apple")
        html = make_ssr_html([SAMPLE_SEARCH_RESULT], 1)
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        progress = []
        fetcher.list_jobs(on_progress=lambda f, t: progress.append((f, t)))
        assert progress == [(1, 1)]


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestFetchJob:
    def test_fetch_job_detail(self):
        fetcher = AppleFetcher("apple")
        mock_response = MagicMock()
        mock_response.json.return_value = {"res": SAMPLE_JOB_DETAIL}
        mock_response.raise_for_status = MagicMock()
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        job = fetcher.fetch_job("200660378-0157")
        assert job.id == "200660378-0157"
        assert job.title == "Manufacturing Design Program Manager (MDPM)"
        # Description should combine summary + stripped HTML description
        assert "Imagine what you could do here" in job.description
        assert "full job description" in job.description
        # HTML should be stripped
        assert "<p>" not in job.description
        assert "<b>" not in job.description

    def test_fetch_job_api_headers(self):
        """Verifies correct headers are sent to the detail API."""
        fetcher = AppleFetcher("apple")
        mock_response = MagicMock()
        mock_response.json.return_value = {"res": SAMPLE_JOB_DETAIL}
        mock_response.raise_for_status = MagicMock()
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        fetcher.fetch_job("200660378-0157")

        call_kwargs = fetcher.client.get.call_args
        assert call_kwargs[1]["headers"]["content-type"] == "application/json"
        assert call_kwargs[1]["headers"]["locale"] == "en-us"


# ---------------------------------------------------------------------------
# fetch_description tests
# ---------------------------------------------------------------------------


class TestFetchDescription:
    def test_fetch_description_returns_text(self):
        fetcher = AppleFetcher("apple")
        mock_response = MagicMock()
        mock_response.json.return_value = {"res": SAMPLE_JOB_DETAIL}
        mock_response.raise_for_status = MagicMock()
        fetcher.client = MagicMock()
        fetcher.client.get.return_value = mock_response

        desc = fetcher.fetch_description("200660378-0157")
        assert "Imagine what you could do here" in desc
        assert "full job description" in desc


# ---------------------------------------------------------------------------
# URL parser tests
# ---------------------------------------------------------------------------


class TestAppleURLParser:
    def test_standard_url(self):
        url = "https://jobs.apple.com/en-us/details/200660378-0157/manufacturing-design-program-manager-mdpm"
        result = parse_url(url)
        assert result == ParsedURL(ats="apple", board="apple", job_id="200660378-0157")

    def test_different_locale(self):
        url = "https://jobs.apple.com/de-de/details/200660378-0157/some-job"
        result = parse_url(url)
        assert result == ParsedURL(ats="apple", board="apple", job_id="200660378-0157")

    def test_no_slug(self):
        url = "https://jobs.apple.com/en-us/details/200660378-0157"
        result = parse_url(url)
        assert result == ParsedURL(ats="apple", board="apple", job_id="200660378-0157")

    def test_non_apple_url_returns_none(self):
        url = "https://boards.greenhouse.io/company/jobs/12345"
        # Should be parsed by greenhouse, not apple
        result = parse_url(url)
        assert result is not None
        assert result.ats == "greenhouse"
