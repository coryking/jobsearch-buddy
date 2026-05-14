"""Tests for Uber careers fetcher."""

from datetime import date
from unittest.mock import MagicMock

from jobbuddy.fetchers.uber import UberFetcher, _parse_date, _build_location
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

JOB_1 = {
    "id": 157996,
    "title": "Senior Software Engineer",
    "description": "## About the Role\n\nWe are looking for a senior engineer.",
    "department": "Engineering",
    "team": "Platform",
    "location": {"city": "San Francisco", "state": "CA", "country": "US"},
    "allLocations": ["San Francisco, CA, US"],
    "level": "IC5",
    "type": "POSTING_TYPE_EXTERNAL",
    "creationDate": "2026-04-01T10:00:00.000Z",
    "updatedDate": "2026-04-22T15:00:00.000Z",
}

JOB_2 = {
    "id": 158000,
    "title": "Product Manager",
    "description": "## PM Role\n\nLead product strategy.",
    "department": "Product",
    "team": None,
    "location": {"city": "New York", "state": "NY", "country": "US"},
    "allLocations": ["New York, NY, US"],
    "level": "M3",
    "type": "POSTING_TYPE_EXTERNAL",
    "creationDate": "2026-03-15T08:00:00.000Z",
    "updatedDate": "2026-04-10T12:00:00.000Z",
}

JOB_NO_LOCATION = {
    "id": 158001,
    "title": "Remote Engineer",
    "description": "Remote position.",
    "department": "Engineering",
    "team": "Cloud",
    "location": None,
    "allLocations": ["Remote, US"],
    "level": "IC4",
    "type": "POSTING_TYPE_EXTERNAL",
    "creationDate": "2026-04-05T00:00:00.000Z",
    "updatedDate": None,
}

SINGLE_PAGE_RESPONSE = {
    "status": "success",
    "data": {
        "results": [JOB_1, JOB_2],
        "totalResults": {"low": 2},
    },
}

TWO_PAGE_RESPONSE_P1 = {
    "status": "success",
    "data": {
        "results": [JOB_1],
        "totalResults": {"low": 2},
    },
}

TWO_PAGE_RESPONSE_P2 = {
    "status": "success",
    "data": {
        "results": [JOB_2],
        "totalResults": {"low": 2},
    },
}


def _make_fetcher() -> UberFetcher:
    fetcher = UberFetcher("uber", "Uber")
    fetcher.client = MagicMock()
    return fetcher


def _mock_post(fetcher: UberFetcher, response_json: dict) -> None:
    resp = MagicMock()
    resp.json.return_value = response_json
    resp.raise_for_status = MagicMock()
    fetcher.client.post.return_value = resp


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2026-04-22T15:00:00.000Z") == date(2026, 4, 22)

    def test_date_only(self):
        assert _parse_date("2026-04-01") == date(2026, 4, 1)

    def test_none_input(self):
        assert _parse_date(None) is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid(self):
        assert _parse_date("not-a-date") is None


class TestBuildLocation:
    def test_dict_location(self):
        assert _build_location(JOB_1) == "San Francisco, CA, US"

    def test_string_location(self):
        item = {"location": "Austin, TX", "allLocations": []}
        assert _build_location(item) == "Austin, TX"

    def test_null_location_falls_back_to_all_locations(self):
        assert _build_location(JOB_NO_LOCATION) == "Remote, US"

    def test_multiple_all_locations_joined(self):
        item = {"location": None, "allLocations": ["San Francisco, CA, US", "New York, NY, US"]}
        assert _build_location(item) == "San Francisco, CA, US; New York, NY, US"

    def test_empty_location_parts_skipped(self):
        item = {"location": {"city": "Seattle", "state": "", "country": "US"}, "allLocations": []}
        assert _build_location(item) == "Seattle, US"

    def test_no_location_at_all(self):
        item = {"location": None, "allLocations": []}
        assert _build_location(item) == ""


# ---------------------------------------------------------------------------
# Fetcher construction
# ---------------------------------------------------------------------------


class TestUberFetcherInit:
    def test_ats_type(self):
        f = UberFetcher("uber")
        assert f.ats_type == "uber"

    def test_descriptions_in_listing(self):
        assert UberFetcher.descriptions_in_listing is True

    def test_name_default(self):
        f = UberFetcher("uber")
        assert f.name == "Uber"

    def test_custom_name(self):
        f = UberFetcher("uber", "Uber Technologies")
        assert f.name == "Uber Technologies"


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_single_page(self):
        fetcher = _make_fetcher()
        _mock_post(fetcher, SINGLE_PAGE_RESPONSE)

        jobs = fetcher.list_jobs()

        assert len(jobs) == 2
        assert jobs[0].id == "157996"
        assert jobs[0].title == "Senior Software Engineer"
        assert jobs[0].location == "San Francisco, CA, US"
        assert jobs[0].department == "Engineering"
        assert jobs[0].team == "Platform"
        assert jobs[0].published_at == date(2026, 4, 1)
        assert jobs[0].last_listing_update == date(2026, 4, 22)
        assert jobs[0].description is not None
        assert "senior engineer" in jobs[0].description

    def test_url_shape(self):
        fetcher = _make_fetcher()
        _mock_post(fetcher, SINGLE_PAGE_RESPONSE)
        jobs = fetcher.list_jobs()
        assert jobs[0].url == "https://www.uber.com/careers/apply/form/157996"
        assert jobs[0].apply_url == jobs[0].url

    def test_null_team_becomes_none(self):
        fetcher = _make_fetcher()
        _mock_post(fetcher, SINGLE_PAGE_RESPONSE)
        jobs = fetcher.list_jobs()
        assert jobs[1].team is None

    def test_progress_callback(self):
        fetcher = _make_fetcher()
        _mock_post(fetcher, SINGLE_PAGE_RESPONSE)

        calls = []
        fetcher.list_jobs(on_progress=lambda f, t: calls.append((f, t)))

        assert calls[-1] == (2, 2)

    def test_pagination_fetches_multiple_pages(self):
        fetcher = _make_fetcher()

        resp1 = MagicMock()
        resp1.json.return_value = TWO_PAGE_RESPONSE_P1
        resp1.raise_for_status = MagicMock()

        resp2 = MagicMock()
        resp2.json.return_value = TWO_PAGE_RESPONSE_P2
        resp2.raise_for_status = MagicMock()

        fetcher.client.post.side_effect = [resp1, resp2]

        jobs = fetcher.list_jobs()
        assert len(jobs) == 2
        ids = {j.id for j in jobs}
        assert "157996" in ids
        assert "158000" in ids

    def test_pagination_sends_offset(self):
        fetcher = _make_fetcher()

        resp1 = MagicMock()
        resp1.json.return_value = TWO_PAGE_RESPONSE_P1
        resp1.raise_for_status = MagicMock()

        resp2 = MagicMock()
        resp2.json.return_value = TWO_PAGE_RESPONSE_P2
        resp2.raise_for_status = MagicMock()

        fetcher.client.post.side_effect = [resp1, resp2]

        fetcher.list_jobs()

        # Second call should include offset
        second_call_kwargs = fetcher.client.post.call_args_list[1]
        payload = second_call_kwargs.kwargs.get("json") or second_call_kwargs.args[1]
        assert payload.get("offset") == 1

    def test_no_description_becomes_none(self):
        fetcher = _make_fetcher()
        response = {
            "status": "success",
            "data": {
                "results": [{**JOB_1, "description": ""}],
                "totalResults": {"low": 1},
            },
        }
        _mock_post(fetcher, response)
        jobs = fetcher.list_jobs()
        assert jobs[0].description is None

    def test_missing_id_item_skipped(self):
        fetcher = _make_fetcher()
        response = {
            "status": "success",
            "data": {
                "results": [{"title": "No ID Job", "description": "X"}, JOB_1],
                "totalResults": {"low": 1},
            },
        }
        _mock_post(fetcher, response)
        jobs = fetcher.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "157996"

    def test_null_location(self):
        fetcher = _make_fetcher()
        response = {
            "status": "success",
            "data": {
                "results": [JOB_NO_LOCATION],
                "totalResults": {"low": 1},
            },
        }
        _mock_post(fetcher, response)
        jobs = fetcher.list_jobs()
        assert jobs[0].location == "Remote, US"
        assert jobs[0].last_listing_update is None


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestURLParsing:
    def test_parse_uber_url(self):
        result = parse_url("https://www.uber.com/careers/apply/form/157996")
        assert result is not None
        assert result.ats == "uber"
        assert result.board == "uber"
        assert result.job_id == "157996"

    def test_parse_uber_url_no_www(self):
        result = parse_url("https://uber.com/careers/apply/form/157996")
        assert result is not None
        assert result.ats == "uber"
        assert result.job_id == "157996"

    def test_non_uber_url_not_matched(self):
        result = parse_url("https://www.uber.com/careers")
        assert result is None
