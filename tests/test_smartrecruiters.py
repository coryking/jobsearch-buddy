"""Tests for SmartRecruiters ATS fetcher and URL parsing."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from jobbuddy.fetchers.smartrecruiters import SmartRecruitersFetcher
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

LISTING_PAGE_0 = {
    "offset": 0,
    "limit": 100,
    "totalFound": 101,
    "content": [
        {
            "id": "3743990012777007",
            "name": "Designer, AI 3",
            "uuid": "82d4783a-1234-5678-9abc-def012345678",
            "refNumber": "R0108838",
            "company": {"identifier": "Wabtec", "name": "Wabtec"},
            "releasedDate": "2026-04-22T19:18:15.881Z",
            "location": {
                "city": "Pittsburgh",
                "region": "PA",
                "country": "us",
                "remote": False,
                "hybrid": False,
                "fullLocation": "Pittsburgh, PA, United States",
            },
            "department": {"id": "868639", "label": "Software Development/Engineering"},
            "function": {"id": "engineering", "label": "Engineering"},
            "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
            "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior Level"},
        },
        {
            "id": "3743990012776976",
            "name": "Supply Chain Coordinator",
            "uuid": "4bfc598d-aaaa-bbbb-cccc-dddddddddddd",
            "refNumber": "R0108405",
            "company": {"identifier": "Wabtec", "name": "Wabtec"},
            "releasedDate": "2026-03-15T10:00:00.000Z",
            "location": {
                "city": "State College",
                "region": "PA",
                "country": "us",
                "remote": False,
                "hybrid": False,
                "fullLocation": "State College, PA, United States",
            },
            "department": {},
            "function": {"id": "other", "label": "Other"},
            "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
        },
    ],
}

LISTING_PAGE_1 = {
    "offset": 100,
    "limit": 100,
    "totalFound": 101,
    "content": [
        {
            "id": "3743990012773442",
            "name": "Senior Director, Transit Services Sales",
            "uuid": "cccccccc-dddd-eeee-ffff-000000000000",
            "refNumber": "R0107999",
            "company": {"identifier": "Wabtec", "name": "Wabtec"},
            "releasedDate": "2026-04-01T08:30:00.000Z",
            "location": {
                "city": "Philadelphia",
                "region": "PA",
                "country": "us",
                "fullLocation": "Philadelphia, PA, United States",
            },
            "department": {"id": "123", "label": "Sales"},
        },
    ],
}

DETAIL_RESPONSE = {
    "id": "3743990012777007",
    "name": "Designer, AI 3",
    "uuid": "82d4783a-1234-5678-9abc-def012345678",
    "company": {"identifier": "Wabtec", "name": "Wabtec"},
    "releasedDate": "2026-04-22T19:18:15.881Z",
    "location": {
        "city": "Pittsburgh",
        "region": "PA",
        "country": "us",
        "fullLocation": "Pittsburgh, PA, United States",
    },
    "department": {"id": "868639", "label": "Software Development/Engineering"},
    "postingUrl": "https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer-ai-3",
    "applyUrl": "https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer-ai-3?oga=true",
    "jobAd": {
        "sections": {
            "companyDescription": {
                "title": "Company Description",
                "text": "<p>Wabtec is a global company.</p>",
            },
            "jobDescription": {
                "title": "Job Description",
                "text": "<p>Design <b>AI systems</b> for rail.</p>",
            },
            "qualifications": {
                "title": "Qualifications",
                "text": "<p>BS in Computer Science required.</p>",
            },
            "additionalInformation": {
                "title": "Additional Information",
                "text": "<p>Salary range: $80,000-$120,000.</p>",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fetcher(**overrides) -> SmartRecruitersFetcher:
    defaults = dict(board="Wabtec", name="Wabtec", country="us")
    defaults.update(overrides)
    board = defaults.pop("board")
    name = defaults.pop("name")
    f = SmartRecruitersFetcher(board, name, **defaults)
    f.client = MagicMock()
    f.backoff_base = 0.01
    return f


def mock_get_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# list_jobs tests
# ---------------------------------------------------------------------------


class TestSmartRecruitersListJobs:
    def test_single_page(self):
        """list_jobs parses a single-page response correctly."""
        fetcher = make_fetcher()
        single_page = {
            "offset": 0,
            "limit": 100,
            "totalFound": 2,
            "content": LISTING_PAGE_0["content"],
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()

        assert len(jobs) == 2
        assert jobs[0].id == "3743990012777007"
        assert jobs[0].title == "Designer, AI 3"
        assert jobs[0].location == "Pittsburgh, PA, United States"
        assert jobs[0].department == "Software Development/Engineering"
        assert jobs[0].published_at == date(2026, 4, 22)
        assert jobs[0].url == "https://jobs.smartrecruiters.com/Wabtec/3743990012777007"
        assert jobs[0].description is None

    def test_pagination(self):
        """list_jobs paginates through multiple pages."""
        fetcher = make_fetcher()
        fetcher.client.get.side_effect = [
            mock_get_response(LISTING_PAGE_0),
            mock_get_response(LISTING_PAGE_1),
        ]

        jobs = fetcher.list_jobs()

        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert ids == {"3743990012777007", "3743990012776976", "3743990012773442"}

    def test_progress_callback(self):
        """list_jobs calls on_progress with (fetched, total)."""
        fetcher = make_fetcher()
        single_page = {
            "offset": 0, "limit": 100, "totalFound": 1,
            "content": [LISTING_PAGE_0["content"][0]],
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        progress = []
        fetcher.list_jobs(on_progress=lambda f, t: progress.append((f, t)))
        assert progress[-1] == (1, 1)

    def test_empty_response(self):
        """list_jobs handles zero results gracefully."""
        fetcher = make_fetcher()
        empty = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        fetcher.client.get.return_value = mock_get_response(empty)

        jobs = fetcher.list_jobs()
        assert jobs == []

    def test_missing_released_date(self):
        """Jobs with no releasedDate get published_at=None."""
        fetcher = make_fetcher()
        no_date = {
            "offset": 0, "limit": 100, "totalFound": 1,
            "content": [{
                "id": "999",
                "name": "Test Job",
                "location": {"fullLocation": "Remote"},
                "department": {},
            }],
        }
        fetcher.client.get.return_value = mock_get_response(no_date)

        jobs = fetcher.list_jobs()
        assert jobs[0].published_at is None

    def test_empty_department(self):
        """Jobs with empty department dict get department=None."""
        fetcher = make_fetcher()
        single_page = {
            "offset": 0, "limit": 100, "totalFound": 1,
            "content": [LISTING_PAGE_0["content"][1]],
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert jobs[0].department is None

    def test_country_filter_passed(self):
        """Country filter is included in API params."""
        fetcher = make_fetcher(country="us")
        single_page = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        fetcher.client.get.return_value = mock_get_response(single_page)

        fetcher.list_jobs()

        call_kwargs = fetcher.client.get.call_args
        assert call_kwargs.kwargs["params"]["country"] == "us"

    def test_no_country_filter(self):
        """Without country config, no country param is sent."""
        fetcher = make_fetcher(country="")
        single_page = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        fetcher.client.get.return_value = mock_get_response(single_page)

        fetcher.list_jobs()

        call_kwargs = fetcher.client.get.call_args
        assert "country" not in call_kwargs.kwargs["params"]

    def test_location_fallback(self):
        """When fullLocation is missing, location is built from parts."""
        fetcher = make_fetcher()
        no_full = {
            "offset": 0, "limit": 100, "totalFound": 1,
            "content": [{
                "id": "111",
                "name": "Test",
                "location": {"city": "Seattle", "region": "WA", "country": "us"},
                "department": {},
            }],
        }
        fetcher.client.get.return_value = mock_get_response(no_full)

        jobs = fetcher.list_jobs()
        assert jobs[0].location == "Seattle, WA, us"

    def test_missing_location(self):
        """Jobs with no location dict get empty string."""
        fetcher = make_fetcher()
        no_loc = {
            "offset": 0, "limit": 100, "totalFound": 1,
            "content": [{
                "id": "222",
                "name": "Test",
                "department": {},
            }],
        }
        fetcher.client.get.return_value = mock_get_response(no_loc)

        jobs = fetcher.list_jobs()
        assert jobs[0].location == ""


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestSmartRecruitersFetchJob:
    def test_fetch_job_detail(self):
        """fetch_job retrieves full details from the detail API."""
        fetcher = make_fetcher()
        fetcher.client.get.return_value = mock_get_response(DETAIL_RESPONSE)

        job = fetcher.fetch_job("3743990012777007")

        assert job.id == "3743990012777007"
        assert job.title == "Designer, AI 3"
        assert job.location == "Pittsburgh, PA, United States"
        assert job.department == "Software Development/Engineering"
        assert job.published_at == date(2026, 4, 22)
        assert job.url == "https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer-ai-3"
        assert job.apply_url == "https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer-ai-3?oga=true"

    def test_description_combines_sections(self):
        """Description combines jobDescription + qualifications + additionalInformation (not companyDescription)."""
        fetcher = make_fetcher()
        fetcher.client.get.return_value = mock_get_response(DETAIL_RESPONSE)

        job = fetcher.fetch_job("3743990012777007")

        assert "AI systems" in job.description
        assert "Computer Science" in job.description
        assert "Salary range" in job.description
        assert "global company" not in job.description

    def test_description_strips_html(self):
        """HTML tags are stripped from description sections."""
        fetcher = make_fetcher()
        fetcher.client.get.return_value = mock_get_response(DETAIL_RESPONSE)

        job = fetcher.fetch_job("3743990012777007")

        assert "<p>" not in job.description
        assert "<b>" not in job.description

    def test_fetch_job_no_description(self):
        """fetch_job handles empty jobAd sections."""
        fetcher = make_fetcher()
        no_desc = {**DETAIL_RESPONSE, "jobAd": {"sections": {}}}
        fetcher.client.get.return_value = mock_get_response(no_desc)

        job = fetcher.fetch_job("3743990012777007")
        assert job.description is None

    def test_fetch_job_not_found(self):
        """fetch_job raises ValueError when detail response is empty."""
        fetcher = make_fetcher()
        fetcher.client.get.return_value = mock_get_response({})

        with pytest.raises(ValueError, match="not found"):
            fetcher.fetch_job("NONEXISTENT")

    def test_posting_url_fallback(self):
        """When postingUrl is missing from detail, falls back to constructed URL."""
        fetcher = make_fetcher()
        no_url = {k: v for k, v in DETAIL_RESPONSE.items() if k not in ("postingUrl", "applyUrl")}
        fetcher.client.get.return_value = mock_get_response(no_url)

        job = fetcher.fetch_job("3743990012777007")
        assert job.url == "https://jobs.smartrecruiters.com/Wabtec/3743990012777007"


# ---------------------------------------------------------------------------
# fetch_description tests
# ---------------------------------------------------------------------------


class TestSmartRecruitersFetchDescription:
    def test_returns_stripped_description(self):
        """fetch_description returns combined, HTML-stripped sections."""
        fetcher = make_fetcher()
        fetcher.client.get.return_value = mock_get_response(DETAIL_RESPONSE)

        desc = fetcher.fetch_description("3743990012777007")

        assert desc is not None
        assert "AI systems" in desc
        assert "Computer Science" in desc
        assert "<p>" not in desc

    def test_empty_sections(self):
        """fetch_description returns None when sections are empty."""
        fetcher = make_fetcher()
        no_desc = {**DETAIL_RESPONSE, "jobAd": {"sections": {}}}
        fetcher.client.get.return_value = mock_get_response(no_desc)

        desc = fetcher.fetch_description("3743990012777007")
        assert desc is None

    def test_no_job_ad(self):
        """fetch_description returns None when jobAd key is missing."""
        fetcher = make_fetcher()
        no_ad = {k: v for k, v in DETAIL_RESPONSE.items() if k != "jobAd"}
        fetcher.client.get.return_value = mock_get_response(no_ad)

        desc = fetcher.fetch_description("3743990012777007")
        assert desc is None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestSmartRecruitersConfig:
    def test_descriptions_in_listing_is_false(self):
        """SmartRecruiters is a stub fetcher — descriptions_in_listing = False."""
        assert SmartRecruitersFetcher.descriptions_in_listing is False

    def test_ats_type(self):
        assert SmartRecruitersFetcher.ats_type == "smartrecruiters"

    def test_api_urls(self):
        fetcher = SmartRecruitersFetcher("Wabtec")
        assert fetcher._list_url() == "https://api.smartrecruiters.com/v1/companies/Wabtec/postings"
        assert fetcher._detail_url("123") == "https://api.smartrecruiters.com/v1/companies/Wabtec/postings/123"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestSmartRecruitersURLParsing:
    def test_standard_posting_url(self):
        result = parse_url("https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer-ai-3")
        assert result is not None
        assert result.ats == "smartrecruiters"
        assert result.board == "Wabtec"
        assert result.job_id == "3743990012777007"

    def test_posting_url_with_query(self):
        result = parse_url("https://jobs.smartrecruiters.com/Wabtec/3743990012777007-designer?oga=true")
        assert result is not None
        assert result.ats == "smartrecruiters"
        assert result.board == "Wabtec"
        assert result.job_id == "3743990012777007"

    def test_different_company(self):
        result = parse_url("https://jobs.smartrecruiters.com/Visa/744000122323342-sr-consultant")
        assert result is not None
        assert result.ats == "smartrecruiters"
        assert result.board == "Visa"
        assert result.job_id == "744000122323342"

    def test_non_smartrecruiters_url(self):
        result = parse_url("https://boards.greenhouse.io/acme/jobs/123")
        assert result is not None
        assert result.ats == "greenhouse"

    def test_unrelated_url(self):
        result = parse_url("https://example.com/jobs/123")
        # Should not match smartrecruiters
        if result is not None:
            assert result.ats != "smartrecruiters"
