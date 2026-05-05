"""Tests for JobSync (Solr-based) ATS fetcher and URL parsing."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.fetchers.jobsync import JobSyncFetcher
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

SEARCH_PAGE_1 = {
    "featured_jobs": [
        {
            "guid": "AAAA1111BBBB2222CCCC3333DDDD4444",
            "reqid": "2026-18661",
            "title_exact": "Passenger Service Agent",
            "title_slug": "passenger-service-agent",
            "description": "## About the Role\n\nServe passengers at the gate.",
            "company_exact": "Horizon Air",
            "buid": 34035,
            "city_exact": "Portland",
            "state_short": "OR",
            "location_exact": "Portland, OR",
            "country_exact": "United States",
            "job_type": "Part-Time",
            "date_new": "2026-04-22T03:00:35Z",
            "score": 1.5,
        },
    ],
    "jobs": [
        {
            "guid": "AAAA1111BBBB2222CCCC3333DDDD4444",
            "reqid": "2026-18661",
            "title_exact": "Passenger Service Agent",
            "title_slug": "passenger-service-agent",
            "description": "## About the Role\n\nServe passengers at the gate.",
            "company_exact": "Horizon Air",
            "buid": 34035,
            "city_exact": "Portland",
            "state_short": "OR",
            "location_exact": "Portland, OR",
            "country_exact": "United States",
            "job_type": "Part-Time",
            "date_new": "2026-04-22T03:00:35Z",
            "score": 1.5,
        },
        {
            "guid": "EEEE5555FFFF6666AAAA7777BBBB8888",
            "reqid": "2026-19000",
            "title_exact": "Aircraft Maintenance Technician",
            "title_slug": "aircraft-maintenance-technician",
            "description": "Maintain and repair aircraft systems.",
            "company_exact": "Alaska Airlines",
            "buid": 1318,
            "city_exact": "Seattle",
            "state_short": "WA",
            "location_exact": "Seattle, WA",
            "country_exact": "United States",
            "job_type": "Full-Time",
            "date_new": "2026-03-15T10:00:00Z",
            "score": 1.2,
        },
    ],
    "pagination": {
        "has_more_pages": True,
        "offset": 0,
        "page": 1,
        "page_size": 10,
        "total": 12,
        "total_pages": 2,
    },
}

SEARCH_PAGE_2 = {
    "featured_jobs": [],
    "jobs": [
        {
            "guid": "11112222333344445555666677778888",
            "reqid": "2026-19500",
            "title_exact": "Flight Attendant",
            "title_slug": "flight-attendant",
            "description": "Provide excellent in-flight service.",
            "company_exact": "Hawaiian Airlines",
            "buid": 59517,
            "city_exact": "Honolulu",
            "state_short": "HI",
            "location_exact": "Honolulu, HI",
            "country_exact": "United States",
            "job_type": "Full-Time",
            "date_new": "2026-04-01T08:30:00Z",
            "score": 1.0,
        },
    ],
    "pagination": {
        "has_more_pages": False,
        "offset": 10,
        "page": 2,
        "page_size": 10,
        "total": 12,
        "total_pages": 2,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_jobsync_fetcher(**overrides) -> JobSyncFetcher:
    defaults = dict(
        board="alaskaair",
        name="Alaska Airlines",
        origin_host="careers.alaskaair.com",
    )
    defaults.update(overrides)
    board = defaults.pop("board")
    name = defaults.pop("name")
    f = JobSyncFetcher(board, name, **defaults)
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


class TestJobSyncListJobs:
    def test_single_page(self):
        """list_jobs parses a single-page response correctly."""
        fetcher = make_jobsync_fetcher()
        single_page = {
            "featured_jobs": [],
            "jobs": [SEARCH_PAGE_1["jobs"][1]],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 1,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()

        assert len(jobs) == 1
        assert jobs[0].id == "EEEE5555FFFF6666AAAA7777BBBB8888"
        assert jobs[0].title == "Aircraft Maintenance Technician"
        assert jobs[0].location == "Seattle, WA"
        assert jobs[0].published_at == date(2026, 3, 15)
        assert "Maintain and repair" in jobs[0].description
        assert jobs[0].url == "https://careers.alaskaair.com/jobs/aircraft-maintenance-technician/EEEE5555FFFF6666AAAA7777BBBB8888/"

    def test_pagination(self):
        """list_jobs paginates through multiple pages."""
        fetcher = make_jobsync_fetcher()
        fetcher.client.get.side_effect = [
            mock_get_response(SEARCH_PAGE_1),
            mock_get_response(SEARCH_PAGE_2),
        ]

        jobs = fetcher.list_jobs()

        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert ids == {
            "AAAA1111BBBB2222CCCC3333DDDD4444",
            "EEEE5555FFFF6666AAAA7777BBBB8888",
            "11112222333344445555666677778888",
        }

    def test_deduplicates_featured_jobs(self):
        """featured_jobs that also appear in jobs are not double-counted."""
        fetcher = make_jobsync_fetcher()
        # Page 1 has AAAA... in both featured_jobs and jobs
        single_page = {
            "featured_jobs": [SEARCH_PAGE_1["featured_jobs"][0]],
            "jobs": SEARCH_PAGE_1["jobs"],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 2,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        guids = [j.id for j in jobs]
        assert len(guids) == len(set(guids))

    def test_progress_callback(self):
        """list_jobs calls on_progress with (fetched, total)."""
        fetcher = make_jobsync_fetcher()
        single_page = {
            "featured_jobs": [],
            "jobs": [SEARCH_PAGE_1["jobs"][1]],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 1,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        progress = []
        fetcher.list_jobs(on_progress=lambda f, t: progress.append((f, t)))
        assert progress[-1] == (1, 1)

    def test_empty_response(self):
        """list_jobs handles zero results gracefully."""
        fetcher = make_jobsync_fetcher()
        empty = {
            "featured_jobs": [],
            "jobs": [],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 0,
                "total_pages": 0,
            },
        }
        fetcher.client.get.return_value = mock_get_response(empty)

        jobs = fetcher.list_jobs()
        assert jobs == []

    def test_x_origin_header_set(self):
        """Construction wires the x-origin / Origin / Referer headers from
        the configured origin_host. Inspects the real client created by
        ``__init__`` rather than the MagicMock the helper substitutes for
        request mocking."""
        fetcher = JobSyncFetcher(
            "alaskaair", "Alaska Airlines", origin_host="careers.alaskaair.com",
        )
        assert fetcher.client.headers["x-origin"] == "careers.alaskaair.com"
        assert fetcher.client.headers["Origin"] == "https://careers.alaskaair.com"
        assert fetcher.client.headers["Referer"] == "https://careers.alaskaair.com/"

    def test_team_from_company_exact(self):
        """company_exact maps to the team field."""
        fetcher = make_jobsync_fetcher()
        single_page = {
            "featured_jobs": [],
            "jobs": [SEARCH_PAGE_1["jobs"][0]],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 1,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert jobs[0].team == "Horizon Air"


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestJobSyncFetchJob:
    def test_fetch_job_found(self):
        """fetch_job finds a job by guid in the listing."""
        fetcher = make_jobsync_fetcher()
        single_page = {
            "featured_jobs": [],
            "jobs": SEARCH_PAGE_1["jobs"],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 2,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        job = fetcher.fetch_job("EEEE5555FFFF6666AAAA7777BBBB8888")
        assert job.id == "EEEE5555FFFF6666AAAA7777BBBB8888"
        assert job.title == "Aircraft Maintenance Technician"

    def test_fetch_job_not_found(self):
        """fetch_job raises ValueError when guid not found."""
        fetcher = make_jobsync_fetcher()
        single_page = {
            "featured_jobs": [],
            "jobs": [SEARCH_PAGE_1["jobs"][0]],
            "pagination": {
                "has_more_pages": False,
                "page": 1,
                "page_size": 10,
                "total": 1,
                "total_pages": 1,
            },
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        with pytest.raises(ValueError, match="not found"):
            fetcher.fetch_job("NONEXISTENT")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestJobSyncConfig:
    def test_requires_origin_host(self):
        """Fetcher raises ValueError when origin_host is not set."""
        fetcher = JobSyncFetcher("test-board", "Test")
        fetcher.client = MagicMock()

        with pytest.raises(ValueError, match="origin_host"):
            fetcher.list_jobs()

    def test_descriptions_in_listing_is_true(self):
        assert JobSyncFetcher.descriptions_in_listing is True

    def test_ats_type(self):
        assert JobSyncFetcher.ats_type == "jobsync"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestJobSyncURLParsing:
    @pytest.fixture(autouse=True)
    def mock_registry(self):
        from jobbuddy.models import Company

        companies = {
            "alaskaair": Company(
                slug="alaskaair", name="Alaska Airlines", ats="jobsync",
                board="alaskaair", origin_host="careers.alaskaair.com",
            ),
        }
        with patch("jobbuddy.registry.load_registry", return_value=companies):
            yield

    def test_alaska_job_url(self):
        result = parse_url(
            "https://careers.alaskaair.com/jobs/passenger-service-agent/53BED5549DD84A0DACA2C0C1EB75E125/"
        )
        assert result is not None
        assert result.ats == "jobsync"
        assert result.board == "alaskaair"
        assert result.job_id == "53BED5549DD84A0DACA2C0C1EB75E125"

    def test_alaska_job_url_no_trailing_slash(self):
        result = parse_url(
            "https://careers.alaskaair.com/jobs/flight-attendant/11112222333344445555666677778888"
        )
        assert result is not None
        assert result.ats == "jobsync"
        assert result.job_id == "11112222333344445555666677778888"

    def test_non_jobsync_url(self):
        result = parse_url("https://example.com/jobs/foo/AABBCCDD11223344")
        if result is not None:
            assert result.ats != "jobsync"
