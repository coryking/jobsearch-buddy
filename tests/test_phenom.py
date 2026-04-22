"""Tests for Phenom People ATS fetcher and URL parsing."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.fetchers.phenom import PhenomFetcher
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

REFINE_SEARCH_PAGE_0 = {
    "refineSearch": {
        "status": 200,
        "hits": 2,
        "totalHits": 3,
        "data": {
            "jobs": [
                {
                    "jobId": "120644BR",
                    "title": "Assembler I- Clean Room",
                    "location": "Endicott, New York, United States",
                    "category": "Manufacturing & Maintenance",
                    "postedDate": "2026-02-02T16:02:16.519+0000",
                    "applyUrl": "https://sjobs.brassring.com/apply/120644BR",
                    "type": "Full-Time",
                    "descriptionTeaser": "Join our team as...",
                    "reqId": "120644BR",
                    "jobSeqNo": "BAE1US120644BREXTERNAL",
                },
                {
                    "jobId": "130999XY",
                    "title": "Software Engineer II",
                    "location": "Arlington, Virginia, United States",
                    "category": "Engineering",
                    "postedDate": "2026-03-15T10:00:00.000+0000",
                    "applyUrl": "https://sjobs.brassring.com/apply/130999XY",
                    "type": "Full-Time",
                    "descriptionTeaser": "Build cool things...",
                    "reqId": "130999XY",
                    "jobSeqNo": "BAE1US130999XYEXTERNAL",
                },
            ],
        },
    },
}

REFINE_SEARCH_PAGE_1 = {
    "refineSearch": {
        "status": 200,
        "hits": 1,
        "totalHits": 3,
        "data": {
            "jobs": [
                {
                    "jobId": "140111ZZ",
                    "title": "Program Manager",
                    "location": "Dallas, Texas, United States",
                    "category": "Program Management",
                    "postedDate": "2026-04-01T08:30:00.000+0000",
                    "applyUrl": "https://sjobs.brassring.com/apply/140111ZZ",
                    "type": "Full-Time",
                    "descriptionTeaser": "Lead programs...",
                    "reqId": "140111ZZ",
                    "jobSeqNo": "BAE1US140111ZZEXTERNAL",
                },
            ],
        },
    },
}

JOB_DETAIL_RESPONSE = {
    "jobDetail": {
        "status": 200,
        "data": {
            "job": {
                "jobId": "120644BR",
                "title": "Assembler I- Clean Room",
                "location": "Endicott, New York, United States",
                "category": "Manufacturing & Maintenance",
                "postedDate": "2026-02-02T16:02:16.519+0000",
                "applyUrl": "https://sjobs.brassring.com/apply/120644BR",
                "description": "<p>This is the <b>full</b> job description.</p>",
                "payRange": "$20-$30/hr",
                "type": "Full-Time",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_phenom_fetcher(**overrides) -> PhenomFetcher:
    defaults = dict(
        board="bae-systems",
        name="BAE Systems",
        careers_url="https://jobs.baesystems.com",
    )
    defaults.update(overrides)
    board = defaults.pop("board")
    name = defaults.pop("name")
    f = PhenomFetcher(board, name, **defaults)
    f.client = MagicMock()
    f.backoff_base = 0.01
    return f


def mock_post_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# list_jobs tests
# ---------------------------------------------------------------------------


class TestPhenomListJobs:
    def test_single_page(self):
        """list_jobs parses a single-page response correctly."""
        fetcher = make_phenom_fetcher()
        # totalHits = 2 with page size 2 => single page
        single_page = {
            "refineSearch": {
                "status": 200,
                "hits": 2,
                "totalHits": 2,
                "data": {
                    "jobs": REFINE_SEARCH_PAGE_0["refineSearch"]["data"]["jobs"][:2],
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(single_page)

        jobs = fetcher.list_jobs()

        assert len(jobs) == 2
        assert jobs[0].id == "120644BR"
        assert jobs[0].title == "Assembler I- Clean Room"
        assert jobs[0].location == "Endicott, New York, United States"
        assert jobs[0].department == "Manufacturing & Maintenance"
        assert jobs[0].published_at == date(2026, 2, 2)
        assert jobs[0].url == "https://jobs.baesystems.com/global/en/job/120644BR"
        assert jobs[0].description is None  # stub fetcher

    def test_pagination(self):
        """list_jobs paginates through multiple pages."""
        fetcher = make_phenom_fetcher()
        fetcher.client.post.side_effect = [
            mock_post_response(REFINE_SEARCH_PAGE_0),
            mock_post_response(REFINE_SEARCH_PAGE_1),
        ]

        jobs = fetcher.list_jobs()

        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert ids == {"120644BR", "130999XY", "140111ZZ"}

    def test_progress_callback(self):
        """list_jobs calls on_progress with (fetched, total)."""
        fetcher = make_phenom_fetcher()
        single_page = {
            "refineSearch": {
                "status": 200,
                "hits": 1,
                "totalHits": 1,
                "data": {
                    "jobs": [REFINE_SEARCH_PAGE_0["refineSearch"]["data"]["jobs"][0]],
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(single_page)

        progress = []
        jobs = fetcher.list_jobs(on_progress=lambda f, t: progress.append((f, t)))

        assert len(jobs) == 1
        assert progress[-1] == (1, 1)

    def test_empty_response(self):
        """list_jobs handles zero results gracefully."""
        fetcher = make_phenom_fetcher()
        empty = {
            "refineSearch": {
                "status": 200,
                "hits": 0,
                "totalHits": 0,
                "data": {"jobs": []},
            },
        }
        fetcher.client.post.return_value = mock_post_response(empty)

        jobs = fetcher.list_jobs()
        assert jobs == []

    def test_missing_posted_date(self):
        """Jobs with no postedDate get published_at=None."""
        fetcher = make_phenom_fetcher()
        no_date = {
            "refineSearch": {
                "status": 200,
                "hits": 1,
                "totalHits": 1,
                "data": {
                    "jobs": [
                        {
                            "jobId": "999",
                            "title": "Test Job",
                            "location": "Remote",
                            "category": "IT",
                        },
                    ],
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(no_date)

        jobs = fetcher.list_jobs()
        assert jobs[0].published_at is None

    def test_custom_locale_and_country(self):
        """Custom locale/country affect job URLs."""
        fetcher = make_phenom_fetcher(
            locale="en_us",
            country="us",
        )
        single_page = {
            "refineSearch": {
                "status": 200,
                "hits": 1,
                "totalHits": 1,
                "data": {
                    "jobs": [REFINE_SEARCH_PAGE_0["refineSearch"]["data"]["jobs"][0]],
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(single_page)

        jobs = fetcher.list_jobs()
        assert jobs[0].url == "https://jobs.baesystems.com/us/en/job/120644BR"


# ---------------------------------------------------------------------------
# fetch_job tests
# ---------------------------------------------------------------------------


class TestPhenomFetchJob:
    def test_fetch_job_detail(self):
        """fetch_job retrieves full details from the detail API."""
        fetcher = make_phenom_fetcher()
        fetcher.client.post.return_value = mock_post_response(JOB_DETAIL_RESPONSE)

        job = fetcher.fetch_job("120644BR")

        assert job.id == "120644BR"
        assert job.title == "Assembler I- Clean Room"
        assert job.location == "Endicott, New York, United States"
        assert job.department == "Manufacturing & Maintenance"
        assert "full" in job.description  # HTML stripped
        assert "<p>" not in job.description  # HTML stripped
        assert "<b>" not in job.description  # HTML stripped
        assert job.salary == "$20-$30/hr"
        assert job.published_at == date(2026, 2, 2)

    def test_fetch_job_no_description(self):
        """fetch_job handles missing description gracefully."""
        fetcher = make_phenom_fetcher()
        no_desc = {
            "jobDetail": {
                "status": 200,
                "data": {
                    "job": {
                        "jobId": "120644BR",
                        "title": "Assembler I",
                        "location": "Endicott, NY",
                    },
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(no_desc)

        job = fetcher.fetch_job("120644BR")
        assert job.description is None

    def test_fetch_job_not_found(self):
        """fetch_job raises ValueError when job detail is empty."""
        fetcher = make_phenom_fetcher()
        empty = {"jobDetail": {"status": 200, "data": {"job": {}}}}
        fetcher.client.post.return_value = mock_post_response(empty)

        with pytest.raises(ValueError, match="not found"):
            fetcher.fetch_job("NONEXISTENT")


# ---------------------------------------------------------------------------
# fetch_description tests
# ---------------------------------------------------------------------------


class TestPhenomFetchDescription:
    def test_fetch_description_returns_stripped_html(self):
        """fetch_description returns stripped HTML from detail API."""
        fetcher = make_phenom_fetcher()
        fetcher.client.post.return_value = mock_post_response(JOB_DETAIL_RESPONSE)

        desc = fetcher.fetch_description("120644BR")

        assert desc is not None
        assert "full" in desc
        assert "<p>" not in desc

    def test_fetch_description_empty(self):
        """fetch_description returns None when no description."""
        fetcher = make_phenom_fetcher()
        no_desc = {
            "jobDetail": {
                "status": 200,
                "data": {
                    "job": {
                        "jobId": "120644BR",
                        "title": "Test",
                    },
                },
            },
        }
        fetcher.client.post.return_value = mock_post_response(no_desc)

        desc = fetcher.fetch_description("120644BR")
        assert desc is None


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestPhenomConfig:
    def test_requires_careers_url(self):
        """Fetcher raises ValueError when careers_url is not set."""
        fetcher = PhenomFetcher("test-board", "Test")
        fetcher.client = MagicMock()

        with pytest.raises(ValueError, match="careers_url"):
            fetcher.list_jobs()

    def test_descriptions_in_listing_is_false(self):
        """Phenom is a stub fetcher — descriptions_in_listing = False."""
        assert PhenomFetcher.descriptions_in_listing is False

    def test_ats_type(self):
        assert PhenomFetcher.ats_type == "phenom"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestPhenomURLParsing:
    @pytest.fixture(autouse=True)
    def mock_registry(self):
        """Provide phenom companies for URL reverse-lookup without DB."""
        from jobbuddy.models import Company

        companies = {
            "bae-systems": Company(
                slug="bae-systems", name="BAE Systems", ats="phenom",
                board="bae-systems", careers_url="https://jobs.baesystems.com",
            ),
            "rtx": Company(
                slug="rtx", name="RTX", ats="phenom",
                board="rtx", careers_url="https://careers.rtx.com",
            ),
        }
        with patch("jobbuddy.registry.load_registry", return_value=companies):
            yield

    def test_bae_systems_url(self):
        result = parse_url("https://jobs.baesystems.com/global/en/job/120644BR")
        assert result is not None
        assert result.ats == "phenom"
        assert result.job_id == "120644BR"

    def test_rtx_url(self):
        result = parse_url("https://careers.rtx.com/global/en/job/01752889")
        assert result is not None
        assert result.ats == "phenom"
        assert result.job_id == "01752889"

    def test_url_with_slug_suffix(self):
        """Phenom URLs sometimes have a slug after the job ID."""
        result = parse_url("https://jobs.baesystems.com/global/en/job/120644BR/assembler-clean-room")
        assert result is not None
        assert result.ats == "phenom"
        assert result.job_id == "120644BR"

    def test_us_locale_url(self):
        result = parse_url("https://careers.rtx.com/us/en/job/01752889")
        assert result is not None
        assert result.ats == "phenom"
        assert result.job_id == "01752889"

    def test_non_phenom_url_not_matched(self):
        """Non-phenom URLs should not match the phenom parser."""
        result = parse_url("https://boards.greenhouse.io/acme/jobs/123")
        assert result is not None
        assert result.ats == "greenhouse"

    def test_random_url_not_matched(self):
        result = parse_url("https://example.com/global/en/job/123")
        # Should not match (hostname not in registry)
        # This may return None or match another parser — either is fine
        # as long as it doesn't falsely match as phenom
        if result is not None:
            assert result.ats != "phenom"
