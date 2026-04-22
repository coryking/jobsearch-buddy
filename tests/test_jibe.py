"""Tests for Jibe (iCIMS Attract) ATS fetcher and URL parsing."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.fetchers.jibe import JibeFetcher
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

LISTING_PAGE_1 = {
    "jobs": [
        {
            "data": {
                "slug": "16625",
                "req_id": "16625",
                "title": "Entry-Level Fabrication Industrial Engineer",
                "description": "<p>This is the <b>full</b> job description.</p>",
                "qualifications": "<p>Required: BS in Engineering</p>",
                "responsibilities": "<p>Design fabrication processes</p>",
                "city": "Wichita",
                "state": "Kansas",
                "country": "United States",
                "categories": [{"name": "Engineering"}],
                "employment_type": "FULL_TIME",
                "posted_date": "2026-04-22T16:15:00+0000",
                "apply_url": "https://careers-spiritaero.icims.com/jobs/16625/login",
                "client_code": "spiritaero",
                "hiring_organization": "Spirit AeroSystems",
                "salary_min_value": 0,
                "salary_max_value": 0,
                "meta_data": {
                    "canonical_url": "https://careers.spiritaero.com/jobs/16625?lang=en-us",
                },
            },
        },
        {
            "data": {
                "slug": "16700",
                "req_id": "16700",
                "title": "Cybersecurity Analyst",
                "description": "<p>Protect our systems.</p>",
                "city": "Dallas",
                "state": "Texas",
                "country": "United States",
                "categories": [{"name": "Cybersecurity"}],
                "employment_type": "FULL_TIME",
                "posted_date": "2026-03-15T10:00:00+0000",
                "apply_url": "https://careers-spiritaero.icims.com/jobs/16700/login",
                "client_code": "spiritaero",
                "hiring_organization": "Spirit AeroSystems",
                "salary_min_value": 80000,
                "salary_max_value": 120000,
            },
        },
    ],
    "totalCount": 3,
    "count": 2,
}

LISTING_PAGE_2 = {
    "jobs": [
        {
            "data": {
                "slug": "16800",
                "req_id": "16800",
                "title": "Painter III",
                "description": "<p>Paint aircraft components.</p>",
                "city": "Wichita",
                "state": "Kansas",
                "country": "United States",
                "categories": [{"name": "Manufacturing & Maintenance"}],
                "employment_type": "FULL_TIME",
                "posted_date": "2026-04-01T08:30:00+0000",
                "apply_url": "https://careers-spiritaero.icims.com/jobs/16800/login",
                "client_code": "spiritaero",
                "hiring_organization": "Spirit AeroSystems",
            },
        },
    ],
    "totalCount": 3,
    "count": 1,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_jibe_fetcher(**overrides) -> JibeFetcher:
    defaults = dict(
        board="spiritaero",
        name="Spirit AeroSystems",
        careers_url="https://careers.spiritaero.com",
    )
    defaults.update(overrides)
    board = defaults.pop("board")
    name = defaults.pop("name")
    f = JibeFetcher(board, name, **defaults)
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


class TestJibeListJobs:
    def test_single_page(self):
        """list_jobs parses a single-page response correctly."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": LISTING_PAGE_1["jobs"][:2],
            "totalCount": 2,
            "count": 2,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()

        assert len(jobs) == 2
        assert jobs[0].id == "16625"
        assert jobs[0].title == "Entry-Level Fabrication Industrial Engineer"
        assert jobs[0].location == "Wichita, Kansas, United States"
        assert jobs[0].department == "Engineering"
        assert jobs[0].published_at == date(2026, 4, 22)
        assert "full" in jobs[0].description
        assert "<p>" not in jobs[0].description
        assert jobs[0].apply_url == "https://careers-spiritaero.icims.com/jobs/16625/login"
        assert jobs[0].url == "https://careers.spiritaero.com/jobs/16625"

    def test_pagination(self):
        """list_jobs paginates through multiple pages."""
        fetcher = make_jibe_fetcher()
        fetcher.client.get.side_effect = [
            mock_get_response(LISTING_PAGE_1),
            mock_get_response(LISTING_PAGE_2),
        ]

        jobs = fetcher.list_jobs()

        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert ids == {"16625", "16700", "16800"}

    def test_progress_callback(self):
        """list_jobs calls on_progress with (fetched, total)."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": [LISTING_PAGE_1["jobs"][0]],
            "totalCount": 1,
            "count": 1,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        progress = []
        fetcher.list_jobs(on_progress=lambda f, t: progress.append((f, t)))
        assert progress[-1] == (1, 1)

    def test_empty_response(self):
        """list_jobs handles zero results gracefully."""
        fetcher = make_jibe_fetcher()
        empty = {"jobs": [], "totalCount": 0, "count": 0}
        fetcher.client.get.return_value = mock_get_response(empty)

        jobs = fetcher.list_jobs()
        assert jobs == []

    def test_salary_range(self):
        """Jobs with salary min/max get formatted salary string."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": [LISTING_PAGE_1["jobs"][1]],
            "totalCount": 1,
            "count": 1,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert jobs[0].salary == "80000 - 120000"

    def test_zero_salary_ignored(self):
        """Jobs with salary_min=0, salary_max=0 get salary=None."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": [LISTING_PAGE_1["jobs"][0]],
            "totalCount": 1,
            "count": 1,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert jobs[0].salary is None

    def test_description_uses_main_field(self):
        """Description comes from the main description field."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": [LISTING_PAGE_1["jobs"][0]],
            "totalCount": 1,
            "count": 1,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert "full" in jobs[0].description

    def test_description_fallback_to_qualifications(self):
        """When description is empty, qualifications/responsibilities are used."""
        fetcher = make_jibe_fetcher()
        job_data = {
            "slug": "99",
            "title": "Test",
            "city": "",
            "state": "",
            "country": "",
            "categories": [],
            "description": "",
            "qualifications": "<p>BS in Engineering</p>",
            "responsibilities": "<p>Design things</p>",
            "apply_url": "https://x.com",
        }
        single_page = {"jobs": [{"data": job_data}], "totalCount": 1, "count": 1}
        fetcher.client.get.return_value = mock_get_response(single_page)

        jobs = fetcher.list_jobs()
        assert "BS in Engineering" in jobs[0].description
        assert "Design things" in jobs[0].description


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestJibeFetchJob:
    def test_fetch_job_found(self):
        """fetch_job finds a job by ID in the listing."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": LISTING_PAGE_1["jobs"],
            "totalCount": 2,
            "count": 2,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        job = fetcher.fetch_job("16700")
        assert job.id == "16700"
        assert job.title == "Cybersecurity Analyst"

    def test_fetch_job_not_found(self):
        """fetch_job raises ValueError when job ID is not in listing."""
        fetcher = make_jibe_fetcher()
        single_page = {
            "jobs": [LISTING_PAGE_1["jobs"][0]],
            "totalCount": 1,
            "count": 1,
        }
        fetcher.client.get.return_value = mock_get_response(single_page)

        with pytest.raises(ValueError, match="not found"):
            fetcher.fetch_job("99999")


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestJibeConfig:
    def test_requires_careers_url(self):
        """Fetcher raises ValueError when careers_url is not set."""
        fetcher = JibeFetcher("test-board", "Test")
        fetcher.client = MagicMock()

        with pytest.raises(ValueError, match="careers_url"):
            fetcher.list_jobs()

    def test_descriptions_in_listing_is_true(self):
        """Jibe is a full fetcher — descriptions_in_listing = True."""
        assert JibeFetcher.descriptions_in_listing is True

    def test_ats_type(self):
        assert JibeFetcher.ats_type == "jibe"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestJibeURLParsing:
    @pytest.fixture(autouse=True)
    def mock_registry(self):
        """Provide Jibe companies for URL reverse-lookup without DB."""
        from jobbuddy.models import Company

        companies = {
            "spiritaero": Company(
                slug="spiritaero", name="Spirit AeroSystems", ats="jibe",
                board="spiritaero", careers_url="https://careers.spiritaero.com",
            ),
        }
        with patch("jobbuddy.registry.load_registry", return_value=companies):
            yield

    def test_spirit_job_url(self):
        result = parse_url("https://careers.spiritaero.com/jobs/16625")
        assert result is not None
        assert result.ats == "jibe"
        assert result.board == "spiritaero"
        assert result.job_id == "16625"

    def test_spirit_job_url_with_lang(self):
        result = parse_url("https://careers.spiritaero.com/jobs/16625?lang=en-us")
        assert result is not None
        assert result.ats == "jibe"
        assert result.job_id == "16625"

    def test_non_jibe_url(self):
        result = parse_url("https://example.com/jobs/123")
        if result is not None:
            assert result.ats != "jibe"

    def test_greenhouse_not_confused(self):
        result = parse_url("https://boards.greenhouse.io/acme/jobs/123")
        assert result is not None
        assert result.ats == "greenhouse"
