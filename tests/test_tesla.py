"""Tests for Tesla careers fetcher and URL parser."""

from unittest.mock import MagicMock

import httpx
import pytest

from jobbuddy.fetchers.tesla import TeslaFetcher, build_description, build_url
from jobbuddy.url import ParsedURL, parse_url


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_STATE = {
    "lookup": {
        "locations": {
            "401022": "Palo Alto, California",
            "63": "Austin, Texas",
        },
        "departments": {
            "4": "Tesla AI",
            "7": "Engineering & Information Technology",
        },
        "types": {"1": "fulltime", "3": "intern"},
        "regions": {"5": "North America"},
    },
    "departments": {"4": ["73", "74"], "7": ["91"]},
    "geo": [],
    "listings": [
        {"id": "224501", "t": "AI Engineer, Manipulation, Optimus", "dp": "4", "f": "73", "l": "401022", "y": 1, "sp": 1, "pu": None},
        {"id": "263687", "t": "Internship, Collision Technician Trainee", "dp": "7", "f": "91", "l": "63", "y": 3, "sp": None, "pu": None},
    ],
}

SAMPLE_JOB_DETAIL = {
    "id": "224501",
    "title": "AI Engineer, Manipulation, Optimus",
    "department": "Tesla AI",
    "jobFamily": "AI",
    "location": "Palo Alto, California",
    "state": "CA",
    "country": "US",
    "timeType": "Full-time",
    "url": "/careers/search/job/ai-engineer-manipulation-optimus-224501",
    "applyUrl": "https://tesla.avature.net/Careers/ApplicationMethods?jobId=224501",
    "description": "",
    "jobDescription": "<p>Tesla AI is solving <b>embodied intelligence</b>.</p>",
    "jobResponsibilities": "<ul><li>Design manipulation algorithms</li></ul>",
    "jobRequirements": "<ul><li>3+ years ML experience</li></ul>",
    "jobCompensationAndBenefits": "<p>$150,000 - $250,000/year</p>",
    "jobVideo": None,
}


# ---------------------------------------------------------------------------
# build_description tests
# ---------------------------------------------------------------------------


class TestBuildDescription:
    def test_all_sections(self):
        desc = build_description(SAMPLE_JOB_DETAIL)
        assert desc is not None
        assert "embodied intelligence" in desc
        assert "Responsibilities:" in desc
        assert "Design manipulation algorithms" in desc
        assert "Requirements:" in desc
        assert "3+ years ML experience" in desc
        assert "Compensation & Benefits:" in desc
        assert "$150,000" in desc
        # HTML should be stripped
        assert "<p>" not in desc
        assert "<b>" not in desc

    def test_description_only(self):
        data = {"jobDescription": "<p>Just a description</p>"}
        desc = build_description(data)
        assert desc == "Just a description"

    def test_empty_returns_none(self):
        assert build_description({}) is None
        assert build_description({"jobDescription": "", "jobResponsibilities": ""}) is None

    def test_skips_empty_fields(self):
        data = {
            "jobDescription": "<p>Main content</p>",
            "jobResponsibilities": "",
            "jobRequirements": "<p>Some reqs</p>",
        }
        desc = build_description(data)
        assert "Main content" in desc
        assert "Responsibilities:" not in desc
        assert "Requirements:" in desc


# ---------------------------------------------------------------------------
# build_url tests
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_with_path(self):
        url = build_url(SAMPLE_JOB_DETAIL)
        assert url == "https://www.tesla.com/careers/search/job/ai-engineer-manipulation-optimus-224501"

    def test_fallback_no_path(self):
        url = build_url({"id": "12345"})
        assert url == "https://www.tesla.com/careers/search/job/12345"


# ---------------------------------------------------------------------------
# Fetcher construction
# ---------------------------------------------------------------------------


class TestFetcherConstruction:
    def test_defaults(self):
        f = TeslaFetcher("tesla")
        assert f.ats_type == "tesla"
        assert f.name == "Tesla"
        assert f.descriptions_in_listing is False

    def test_custom_name(self):
        f = TeslaFetcher("tesla", name="Tesla Motors")
        assert f.name == "Tesla Motors"


# ---------------------------------------------------------------------------
# list_jobs tests
# ---------------------------------------------------------------------------


class TestListJobs:
    def _mock_fetcher(self) -> TeslaFetcher:
        f = TeslaFetcher("tesla")
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_STATE
        mock_resp.raise_for_status = MagicMock()
        f.client = MagicMock()
        f.client.get.return_value = mock_resp
        return f

    def test_parses_all_listings(self):
        f = self._mock_fetcher()
        jobs = f.list_jobs()
        assert len(jobs) == 2

    def test_resolves_location_lookup(self):
        f = self._mock_fetcher()
        jobs = f.list_jobs()
        ai_job = next(j for j in jobs if j.id == "224501")
        assert ai_job.location == "Palo Alto, California"

    def test_resolves_department_lookup(self):
        f = self._mock_fetcher()
        jobs = f.list_jobs()
        ai_job = next(j for j in jobs if j.id == "224501")
        assert ai_job.department == "Tesla AI"

    def test_builds_urls(self):
        f = self._mock_fetcher()
        jobs = f.list_jobs()
        ai_job = next(j for j in jobs if j.id == "224501")
        assert ai_job.url == "https://www.tesla.com/careers/search/job/224501"
        assert "avature.net" in ai_job.apply_url

    def test_no_descriptions(self):
        f = self._mock_fetcher()
        jobs = f.list_jobs()
        assert all(j.description is None for j in jobs)

    def test_progress_callback(self):
        f = self._mock_fetcher()
        progress = []
        f.list_jobs(on_progress=lambda fetched, total: progress.append((fetched, total)))
        assert progress == [(2, 2)]

    def test_unknown_lookup_ids(self):
        f = TeslaFetcher("tesla")
        state = {
            "lookup": {"locations": {}, "departments": {}},
            "listings": [{"id": "99", "t": "Test", "dp": "999", "l": "999", "y": None}],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = state
        mock_resp.raise_for_status = MagicMock()
        f.client = MagicMock()
        f.client.get.return_value = mock_resp

        jobs = f.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].location == ""
        assert jobs[0].department is None


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestFetchJob:
    def test_fetch_job_detail(self):
        f = TeslaFetcher("tesla")
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_JOB_DETAIL
        mock_resp.raise_for_status = MagicMock()
        f.client = MagicMock()
        f.client.get.return_value = mock_resp

        job = f.fetch_job("224501")
        assert job.id == "224501"
        assert job.title == "AI Engineer, Manipulation, Optimus"
        assert job.location == "Palo Alto, California"
        assert job.department == "Tesla AI"
        assert job.team == "AI"
        assert "embodied intelligence" in job.description
        assert "<p>" not in job.description

    def test_fetch_job_url(self):
        f = TeslaFetcher("tesla")
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_JOB_DETAIL
        mock_resp.raise_for_status = MagicMock()
        f.client = MagicMock()
        f.client.get.return_value = mock_resp

        job = f.fetch_job("224501")
        assert "tesla.com/careers/search/job/" in job.url
        assert job.apply_url == "https://tesla.avature.net/Careers/ApplicationMethods?jobId=224501"


# ---------------------------------------------------------------------------
# fetch_description tests
# ---------------------------------------------------------------------------


class TestFetchDescription:
    def test_returns_description_text(self):
        f = TeslaFetcher("tesla")
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_JOB_DETAIL
        mock_resp.raise_for_status = MagicMock()
        f.client = MagicMock()
        f.client.get.return_value = mock_resp

        desc = f.fetch_description("224501")
        assert "embodied intelligence" in desc
        assert "Responsibilities:" in desc


# ---------------------------------------------------------------------------
# URL parser tests
# ---------------------------------------------------------------------------


class TestTeslaURLParser:
    def test_standard_url(self):
        url = "https://www.tesla.com/careers/search/job/internship-collision-technician-trainee-summer-2026-263687"
        result = parse_url(url)
        assert result == ParsedURL(ats="tesla", board="tesla", job_id="263687")

    def test_no_www(self):
        url = "https://tesla.com/careers/search/job/ai-engineer-224501"
        result = parse_url(url)
        assert result == ParsedURL(ats="tesla", board="tesla", job_id="224501")

    def test_id_only(self):
        url = "https://www.tesla.com/careers/search/job/224501"
        result = parse_url(url)
        assert result == ParsedURL(ats="tesla", board="tesla", job_id="224501")

    def test_non_tesla_url_returns_none(self):
        result = parse_url("https://example.com/careers/search/job/12345")
        assert result is None
