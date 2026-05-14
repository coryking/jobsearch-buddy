"""Tests for Oracle Taleo Enterprise ATS fetcher."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from jobbuddy.fetchers.taleo import TaleoFetcher, _parse_date
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

FTL_HTML_WITH_PORTAL = """\
<html>
<head><title>Textron Aviation - Job Search</title></head>
<body>
<script>
var jobSearchUrl = "/careersection/rest/jobboard/searchjobs?lang=en&portal=2140452562";
</script>
</body>
</html>
"""

FTL_HTML_WITH_PORTAL_VAR = """\
<html>
<body>
<script>
var config = {
  "portal": "9876543210",
  "lang": "en"
};
</script>
</body>
</html>
"""

FTL_HTML_NO_PORTAL = """\
<html><body><p>Job search page</p></body></html>
"""

DETAIL_HTML_NESTED = """\
<html>
<body>
<div class="job-description">
  <div class="section-header">Responsibilities</div>
  <div class="section-body">
    <ul><li>Design systems</li><li>Review code</li></ul>
  </div>
  <div class="section-header">Requirements</div>
  <div class="section-body">
    <p>5+ years experience</p>
  </div>
</div>
</body>
</html>
"""

DETAIL_HTML_DIV = """\
<html>
<body>
<h1 class="job-title">Aircraft Systems Engineer</h1>
<div class="job-description">
  <p>We are looking for an experienced engineer to join our team.</p>
  <ul>
    <li>Design aircraft systems</li>
    <li>Collaborate with cross-functional teams</li>
  </ul>
  <p>Requirements: 5+ years experience in aerospace.</p>
</div>
</body>
</html>
"""

DETAIL_HTML_REQUISITION = """\
<html>
<body>
<div id="requisitionDescriptionInterface">
  <p>We seek an aviation MRO specialist.</p>
  <p>Responsibilities include maintenance and repair oversight.</p>
</div>
</body>
</html>
"""

DETAIL_HTML_NO_DESCRIPTION = """\
<html><body><p>Detailed job info here without proper markers</p></body></html>
"""


# ---------------------------------------------------------------------------
# Sample REST API responses
# ---------------------------------------------------------------------------

PAGE_1_RESPONSE = {
    "requisitionList": [
        {
            "contestNo": "2026-TECH-001",
            "requisitionTitle": "Aircraft Systems Engineer",
            "primaryLocation": "Wichita, KS",
            "jobField": "Engineering",
            "dates": {"openingDate": "Apr 01, 2026", "closingDate": None},
        },
        {
            "contestNo": "2026-TECH-002",
            "requisitionTitle": "Software Engineer",
            "primaryLocation": "Dallas, TX",
            "jobField": "IT",
            "dates": {"openingDate": "Mar 15, 2026"},
        },
    ],
    "requisitionCount": 3,
}

PAGE_2_RESPONSE = {
    "requisitionList": [
        {
            "contestNo": "2026-TECH-003",
            "requisitionTitle": "Supply Chain Analyst",
            "primaryLocation": "Wichita, KS",
            "jobField": "Operations",
            "dates": {"openingDate": "Apr 22, 2026"},
        },
    ],
    "requisitionCount": 3,
}

SINGLE_PAGE_RESPONSE = {
    "requisitionList": [
        {
            "contestNo": "2026-TECH-001",
            "requisitionTitle": "Aircraft Systems Engineer",
            "primaryLocation": "Wichita, KS",
            "jobField": "Engineering",
            "dates": {"openingDate": "Apr 01, 2026", "closingDate": None},
        },
    ],
    "requisitionCount": 1,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fetcher(board: str = "textron", section: str = "textron") -> TaleoFetcher:
    fetcher = TaleoFetcher(board, "Textron Aviation", taleo_section=section)
    fetcher.client = MagicMock()
    return fetcher


def _mock_get(fetcher: TaleoFetcher, html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    fetcher.client.get.return_value = resp
    return resp


def _mock_post(fetcher: TaleoFetcher, response_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = response_json
    resp.raise_for_status = MagicMock()
    fetcher.client.post.return_value = resp
    return resp


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_standard_long_date(self):
        assert _parse_date("Apr 01, 2026") == date(2026, 4, 1)

    def test_slash_date(self):
        assert _parse_date("04/01/2026") == date(2026, 4, 1)

    def test_iso_date(self):
        assert _parse_date("2026-04-01") == date(2026, 4, 1)

    def test_none(self):
        assert _parse_date(None) is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid(self):
        assert _parse_date("not a date") is None


# ---------------------------------------------------------------------------
# Portal ID discovery
# ---------------------------------------------------------------------------


class TestPortalDiscovery:
    def test_discovers_from_rest_url(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_WITH_PORTAL)
        portal_id = fetcher._get_portal_id()
        assert portal_id == "2140452562"

    def test_discovers_from_js_variable(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_WITH_PORTAL_VAR)
        portal_id = fetcher._get_portal_id()
        assert portal_id == "9876543210"

    def test_raises_when_not_found(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_NO_PORTAL)
        with pytest.raises(ValueError, match="Cannot discover Taleo portal ID"):
            fetcher._get_portal_id()

    def test_portal_override_skips_discovery(self):
        fetcher = TaleoFetcher("textron", taleo_section="textron", taleo_portal="STATIC123")
        fetcher.client = MagicMock()
        portal_id = fetcher._get_portal_id()
        assert portal_id == "STATIC123"
        fetcher.client.get.assert_not_called()

    def test_caches_portal_id(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_WITH_PORTAL)
        fetcher._get_portal_id()
        fetcher._get_portal_id()
        # Should only fetch the FTL page once
        assert fetcher.client.get.call_count == 1

    def test_uses_taleo_section_in_ftl_url(self):
        fetcher = _make_fetcher(board="aarcorp", section="2")
        _mock_get(fetcher, FTL_HTML_WITH_PORTAL)
        fetcher._get_portal_id()
        called_url = fetcher.client.get.call_args[0][0]
        assert "aarcorp.taleo.net" in called_url
        assert "/careersection/2/" in called_url


# ---------------------------------------------------------------------------
# Requisition parsing
# ---------------------------------------------------------------------------


class TestRequisitionParsing:
    def test_full_requisition(self):
        fetcher = _make_fetcher()
        item = {
            "contestNo": "2026-TECH-001",
            "requisitionTitle": "Aircraft Systems Engineer",
            "primaryLocation": "Wichita, KS",
            "jobField": "Engineering",
            "dates": {"openingDate": "Apr 01, 2026"},
        }
        job = fetcher._parse_requisition(item)
        assert job is not None
        assert job.id == "2026-TECH-001"
        assert job.title == "Aircraft Systems Engineer"
        assert job.location == "Wichita, KS"
        assert job.department == "Engineering"
        assert job.published_at == date(2026, 4, 1)
        assert "jobdetail.ftl" in job.url
        assert "job=2026-TECH-001" in job.url

    def test_alternate_field_names(self):
        fetcher = _make_fetcher()
        item = {
            "jobId": 99999,
            "jobTitle": "MRO Specialist",
            "location": "Dallas, TX",
            "postingDate": "2026-03-15",
        }
        job = fetcher._parse_requisition(item)
        assert job is not None
        assert job.id == "99999"
        assert job.title == "MRO Specialist"
        assert job.location == "Dallas, TX"
        assert job.published_at == date(2026, 3, 15)

    def test_no_id_returns_none(self):
        fetcher = _make_fetcher()
        job = fetcher._parse_requisition({"requisitionTitle": "Engineer"})
        assert job is None

    def test_url_encodes_section(self):
        fetcher = _make_fetcher(board="aarcorp", section="2")
        item = {"contestNo": "123", "requisitionTitle": "Test", "primaryLocation": ""}
        job = fetcher._parse_requisition(item)
        assert "/careersection/2/jobdetail.ftl" in job.url


# ---------------------------------------------------------------------------
# Description extraction
# ---------------------------------------------------------------------------


class TestDescriptionExtraction:
    def test_div_class_job_description(self):
        fetcher = _make_fetcher()
        desc = fetcher._extract_description(DETAIL_HTML_DIV)
        assert desc is not None
        assert "experienced engineer" in desc
        assert "Design aircraft systems" in desc

    def test_requisition_description_interface(self):
        fetcher = _make_fetcher()
        desc = fetcher._extract_description(DETAIL_HTML_REQUISITION)
        assert desc is not None
        assert "aviation MRO specialist" in desc

    def test_no_known_marker(self):
        fetcher = _make_fetcher()
        desc = fetcher._extract_description(DETAIL_HTML_NO_DESCRIPTION)
        assert desc is None

    def test_nested_divs_fully_extracted(self):
        """Depth-counting should capture all nested sections, not just the first div."""
        fetcher = _make_fetcher()
        desc = fetcher._extract_description(DETAIL_HTML_NESTED)
        assert desc is not None
        assert "Design systems" in desc
        assert "Requirements" in desc
        assert "5+ years experience" in desc


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    def _setup_list(self, fetcher: TaleoFetcher, page_responses: list[dict]) -> None:
        """Wire up a fetcher: first GET for portal discovery, then POSTs for pages."""
        get_resp = MagicMock()
        get_resp.text = FTL_HTML_WITH_PORTAL
        get_resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = get_resp

        post_resps = []
        for data in page_responses:
            r = MagicMock()
            r.json.return_value = data
            r.raise_for_status = MagicMock()
            post_resps.append(r)
        fetcher.client.post.side_effect = post_resps

    def test_single_page(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])

        jobs = fetcher.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "2026-TECH-001"
        assert jobs[0].title == "Aircraft Systems Engineer"

    def test_pagination(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [PAGE_1_RESPONSE, PAGE_2_RESPONSE])

        jobs = fetcher.list_jobs()
        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert "2026-TECH-001" in ids
        assert "2026-TECH-003" in ids

    def test_rest_endpoint_includes_portal(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])
        fetcher.list_jobs()
        called_url = fetcher.client.post.call_args[0][0]
        assert "portal=2140452562" in called_url

    def test_page_body_increments(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [PAGE_1_RESPONSE, PAGE_2_RESPONSE])
        fetcher.list_jobs()

        first_body = fetcher.client.post.call_args_list[0].kwargs.get("json") or \
            fetcher.client.post.call_args_list[0].args[1]
        assert first_body["pageNo"] == 1

    def test_progress_callback(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])

        calls = []
        fetcher.list_jobs(on_progress=lambda f, t: calls.append((f, t)))
        assert calls[-1] == (1, 1)


# ---------------------------------------------------------------------------
# fetch_description
# ---------------------------------------------------------------------------


class TestFetchDescription:
    def test_fetches_detail_page(self):
        fetcher = _make_fetcher()
        resp = MagicMock()
        resp.text = DETAIL_HTML_DIV
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        desc = fetcher.fetch_description("2026-TECH-001")
        assert desc is not None
        assert "experienced engineer" in desc

    def test_uses_metadata_url(self):
        fetcher = _make_fetcher()
        resp = MagicMock()
        resp.text = DETAIL_HTML_DIV
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        custom_url = "https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=2026-TECH-001&lang=en"
        fetcher.fetch_description("2026-TECH-001", metadata={"url": custom_url})
        fetcher.client.get.assert_called_with(custom_url)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestURLParsing:
    def test_parse_textron_url(self):
        url = "https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=2026-TECH-001&lang=en"
        result = parse_url(url)
        assert result is not None
        assert result.ats == "taleo"
        assert result.board == "textron"
        assert result.job_id == "2026-TECH-001"

    def test_parse_aarcorp_url(self):
        url = "https://aarcorp.taleo.net/careersection/2/jobdetail.ftl?job=12345&lang=en"
        result = parse_url(url)
        assert result is not None
        assert result.ats == "taleo"
        assert result.board == "aarcorp"
        assert result.job_id == "12345"

    def test_jobsearch_ftl_not_matched(self):
        url = "https://textron.taleo.net/careersection/textron/jobsearch.ftl?lang=en"
        result = parse_url(url)
        assert result is None

    def test_non_taleo_url_not_matched(self):
        result = parse_url("https://jobs.example.com/job/123")
        assert result is None
