"""Tests for the Ashby fetcher.

Some Ashby boards disable the public posting API
(`api.ashbyhq.com/posting-api/job-board/<board>` returns 404) while the
hosted job board at `jobs.ashbyhq.com/<board>` stays live, served by the
same `non-user-graphql` endpoint the fetcher already uses for application
forms. The fetcher falls back to that GraphQL surface when the posting
API 404s, so a disabled posting API reads as "fetch via GraphQL", not
"company left Ashby".
"""

from unittest.mock import MagicMock

import httpx
import pytest

from jobbuddy.fetchers.ashby import AshbyFetcher


SAMPLE_POSTING_API = {
    "jobs": [
        {
            "id": "aaa-111",
            "title": "Backend Engineer",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/acme/aaa-111",
            "applyUrl": "https://jobs.ashbyhq.com/acme/aaa-111/application",
            "publishedAt": "2026-05-01T00:00:00Z",
            "department": "Engineering",
            "team": "Platform",
            "compensation": {"compensationTierSummary": "$150K – $200K"},
            "descriptionPlain": "Build the backend.",
        },
    ]
}

GRAPHQL_BOARD = {
    "data": {
        "jobBoard": {
            "teams": [
                {"id": "team-1", "name": "Sales"},
                {"id": "team-2", "name": "Engineering"},
            ],
            "jobPostings": [
                {
                    "id": "bbb-222",
                    "title": "Account Executive",
                    "teamId": "team-1",
                    "locationName": "San Francisco, CA",
                    "employmentType": "FullTime",
                    "compensationTierSummary": None,
                },
                {
                    "id": "ccc-333",
                    "title": "Staff Engineer",
                    "teamId": "team-2",
                    "locationName": "Remote",
                    "employmentType": "FullTime",
                    "compensationTierSummary": "$180K – $240K",
                },
            ],
        }
    }
}

GRAPHQL_DETAILS = {
    "bbb-222": {
        "data": {
            "jobPosting": {
                "id": "bbb-222",
                "title": "Account Executive",
                "departmentName": "Sales",
                "locationName": "San Francisco, CA",
                "descriptionHtml": "<p>Sell the <strong>product</strong>.</p>",
                "compensationTierSummary": None,
                "publishedDate": "2026-04-15",
            }
        }
    },
    "ccc-333": {
        "data": {
            "jobPosting": {
                "id": "ccc-333",
                "title": "Staff Engineer",
                "departmentName": "Engineering",
                "locationName": "Remote",
                "descriptionHtml": "<p>Own hard problems.</p>",
                "compensationTierSummary": "$180K – $240K",
                "publishedDate": "2026-05-20",
            }
        }
    },
}


def _make_fetcher() -> AshbyFetcher:
    f = AshbyFetcher("acme")
    f.client = MagicMock()
    return f


def _mock_response(json_data, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def test_posting_api_happy_path():
    """Posting API available: jobs come from it, no GraphQL calls made."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response(SAMPLE_POSTING_API)

    jobs = f.list_jobs()

    assert len(jobs) == 1
    j = jobs[0]
    assert j.id == "aaa-111"
    assert j.salary == "$150K – $200K"
    assert j.description == "Build the backend."
    f.client.post.assert_not_called()


def test_posting_api_404_falls_back_to_graphql():
    """Posting API 404 → list via GraphQL board + per-job detail calls."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response({}, status_code=404)

    def graphql_post(url, json=None, **kwargs):
        op = json["operationName"]
        if op == "ApiJobBoardWithTeams":
            return _mock_response(GRAPHQL_BOARD)
        if op == "ApiJobPosting":
            return _mock_response(GRAPHQL_DETAILS[json["variables"]["jobPostingId"]])
        raise AssertionError(f"unexpected GraphQL op {op}")

    f.client.post.side_effect = graphql_post

    jobs = f.list_jobs()

    assert [j.id for j in jobs] == ["bbb-222", "ccc-333"]

    ae = jobs[0]
    assert ae.title == "Account Executive"
    assert ae.location == "San Francisco, CA"
    assert ae.url == "https://jobs.ashbyhq.com/acme/bbb-222"
    assert ae.apply_url == "https://jobs.ashbyhq.com/acme/bbb-222/application"
    assert ae.department == "Sales"
    assert ae.team == "Sales"  # resolved from board teams via teamId
    assert ae.salary is None
    assert ae.description == "Sell the product."  # HTML stripped
    assert str(ae.published_at) == "2026-04-15"

    se = jobs[1]
    assert se.salary == "$180K – $240K"
    assert se.team == "Engineering"
    assert se.description == "Own hard problems."


def test_posting_api_non_404_error_raises():
    """A 500 from the posting API is a real error, not a fallback trigger."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response({}, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        f.list_jobs()
    f.client.post.assert_not_called()


def test_graphql_fallback_board_gone_raises():
    """404 on posting API AND null jobBoard from GraphQL = board truly gone."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response({}, status_code=404)
    f.client.post.return_value = _mock_response({"data": {"jobBoard": None}})

    with pytest.raises(ValueError, match="acme"):
        f.list_jobs()


def test_fetch_job_uses_fallback_listing():
    """fetch_job filters list_jobs, so the fallback path serves it too."""
    f = _make_fetcher()
    f.client.get.return_value = _mock_response({}, status_code=404)

    def graphql_post(url, json=None, **kwargs):
        op = json["operationName"]
        if op == "ApiJobBoardWithTeams":
            return _mock_response(GRAPHQL_BOARD)
        return _mock_response(GRAPHQL_DETAILS[json["variables"]["jobPostingId"]])

    f.client.post.side_effect = graphql_post

    job = f.fetch_job("ccc-333")
    assert job.title == "Staff Engineer"
