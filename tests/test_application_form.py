"""Tests for the get_application_form MCP tool and fetcher hooks.

Three ATSes expose application-form metadata anonymously: Greenhouse, Ashby,
and Rippling. For each, the fetcher returns the raw vendor payload — no
normalization across vendors. Every other ATS raises
`ApplicationFormNotSupported`.

Fixtures under `tests/fixtures/application_form/` were captured from live
endpoints once and committed so these tests run offline.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from jobbuddy.fetchers.ashby import AshbyFetcher
from jobbuddy.fetchers.base import ApplicationFormNotSupported
from jobbuddy.fetchers.greenhouse import GreenhouseFetcher
from jobbuddy.fetchers.rippling import RipplingFetcher
from jobbuddy.fetchers.workday import WorkdayFetcher

FIXTURES = Path(__file__).parent / "fixtures" / "application_form"


def _mock_json_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    return resp


def _mock_text_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    return resp


class TestGreenhouseApplicationForm:
    """Greenhouse returns the job posting + form fields when called with
    `?questions=true`. We pass the parsed JSON through unchanged."""

    def test_returns_raw_vendor_payload(self):
        sample = json.loads((FIXTURES / "greenhouse_sample.json").read_text())
        f = GreenhouseFetcher("anthropic")
        f.client = MagicMock()
        f.client.get.return_value = _mock_json_response(sample)

        result = f.fetch_application_form("anthropic", "5197597008")

        # Hit the expected endpoint with the questions param
        called_url = f.client.get.call_args[0][0]
        assert "boards-api.greenhouse.io/v1/boards/anthropic/jobs/5197597008" in called_url
        assert "questions=true" in called_url

        # Returned payload is the raw vendor response — keys preserved
        assert "questions" in result
        assert "demographic_questions" in result
        assert "compliance" in result
        assert "location_questions" in result

    def test_propagates_404(self):
        f = GreenhouseFetcher("anthropic")
        f.client = MagicMock()
        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        f.client.get.return_value = resp
        with pytest.raises(httpx.HTTPStatusError):
            f.fetch_application_form("anthropic", "nope")


class TestAshbyApplicationForm:
    """Ashby's GraphQL endpoint returns `data.jobPosting.applicationForm`.
    We return the `applicationForm` subtree unchanged (the JD itself is
    available via list_jobs / fetch_job)."""

    def test_returns_application_form_subtree(self):
        sample = json.loads((FIXTURES / "ashby_sample.json").read_text())
        f = AshbyFetcher("ashby")
        f.client = MagicMock()
        f.client.post.return_value = _mock_json_response(sample)

        result = f.fetch_application_form(
            "ashby", "7458d4e9-da2e-47bd-98cb-adfda43d42b2"
        )

        # Verify the POST went to the GraphQL endpoint with the right variables
        call = f.client.post.call_args
        called_url = call[0][0]
        assert "jobs.ashbyhq.com/api/non-user-graphql" in called_url
        body = call[1].get("json") or (json.loads(call[1]["content"]) if "content" in call[1] else None)
        assert body is not None
        assert body["variables"]["organizationHostedJobsPageName"] == "ashby"
        assert body["variables"]["jobPostingId"] == "7458d4e9-da2e-47bd-98cb-adfda43d42b2"

        # Raw application-form subtree returned
        assert "sections" in result
        assert isinstance(result["sections"], list)

    def test_raises_when_form_missing(self):
        f = AshbyFetcher("ashby")
        f.client = MagicMock()
        f.client.post.return_value = _mock_json_response(
            {"data": {"jobPosting": None}}
        )
        with pytest.raises(ApplicationFormNotSupported):
            f.fetch_application_form("ashby", "00000000-0000-0000-0000-000000000000")

    def test_raises_when_form_null(self):
        f = AshbyFetcher("ashby")
        f.client = MagicMock()
        f.client.post.return_value = _mock_json_response(
            {"data": {"jobPosting": {"id": "x", "title": "T", "applicationForm": None}}}
        )
        with pytest.raises(ApplicationFormNotSupported):
            f.fetch_application_form("ashby", "x")


class TestRipplingApplicationForm:
    """Rippling embeds form data in the listing HTML under
    `__NEXT_DATA__` at `props.pageProps.apiData.jobPost.activeJobApplication`.
    We return the full `activeJobApplication` subtree."""

    def test_returns_active_job_application(self):
        html = (FIXTURES / "rippling_sample.html").read_text()
        f = RipplingFetcher("kai-cyber-inc")
        f.client = MagicMock()
        f.client.get.return_value = _mock_text_response(html)

        result = f.fetch_application_form(
            "kai-cyber-inc", "4813f4c2-4f88-4996-baf1-06c81a5518b1"
        )

        # Returned subtree carries the question buckets unchanged
        assert "customQuestions" in result
        assert "additionalQuestions" in result

        # Verify the GET hit the listing HTML page
        called_url = f.client.get.call_args[0][0]
        assert "ats.rippling.com/kai-cyber-inc/jobs/4813f4c2-4f88-4996-baf1-06c81a5518b1" in called_url

    def test_raises_when_next_data_missing(self):
        f = RipplingFetcher("kai-cyber-inc")
        f.client = MagicMock()
        f.client.get.return_value = _mock_text_response(
            "<html><body>no next data here</body></html>"
        )
        with pytest.raises(ApplicationFormNotSupported):
            f.fetch_application_form("kai-cyber-inc", "abc")

    def test_raises_when_active_application_missing(self):
        # Valid NEXT_DATA shape but no activeJobApplication inside
        next_data = {"props": {"pageProps": {"apiData": {"jobPost": {}}}}}
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data)
            + "</script></html>"
        )
        f = RipplingFetcher("kai-cyber-inc")
        f.client = MagicMock()
        f.client.get.return_value = _mock_text_response(html)
        with pytest.raises(ApplicationFormNotSupported):
            f.fetch_application_form("kai-cyber-inc", "abc")


class TestUnsupportedFetcher:
    """Every fetcher that doesn't override fetch_application_form must raise
    ApplicationFormNotSupported. Workday stands in for the unsupported pool."""

    def test_workday_raises(self):
        f = WorkdayFetcher("acme")
        with pytest.raises(ApplicationFormNotSupported):
            f.fetch_application_form("acme", "JR123")


class TestMcpTool:
    """The MCP tool wires URL → fetcher and turns each failure mode into a
    string the calling LLM can show the user."""

    def test_unrecognized_url(self, monkeypatch):
        # Trigger registration; mcp_tools.jobs decorates on import
        from jobbuddy.mcp_tools import jobs as jobs_module

        result = jobs_module.get_application_form(
            url="https://example.com/not-a-job"
        )
        assert "URL" in result or "recognize" in result.lower()

    def test_unsupported_ats(self, monkeypatch):
        from jobbuddy.mcp_tools import jobs as jobs_module

        # A Workday URL parses but Workday doesn't support form fetch
        url = "https://acme.wd1.myworkdayjobs.com/en-US/acme/job/Senior_JR123"
        result = jobs_module.get_application_form(url=url)
        assert "does not expose" in result or "unsupported" in result.lower() or "anonymously" in result

    def test_supported_ats_returns_json(self, monkeypatch):
        from jobbuddy import fetchers as fetchers_pkg
        from jobbuddy.mcp_tools import jobs as jobs_module

        sample = json.loads((FIXTURES / "greenhouse_sample.json").read_text())

        class FakeGH:
            ats_type = "greenhouse"
            def __init__(self, *a, **kw): pass
            def fetch_application_form(self, board, job_id):
                return sample

        monkeypatch.setitem(fetchers_pkg._REGISTRY, "greenhouse", FakeGH)

        url = "https://job-boards.greenhouse.io/anthropic/jobs/5197597008"
        result = jobs_module.get_application_form(url=url)
        parsed = json.loads(result)
        assert parsed["id"] == sample["id"]
