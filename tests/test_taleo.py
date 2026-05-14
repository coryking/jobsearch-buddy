"""Tests for Oracle Taleo Enterprise ATS fetcher.

Fixtures match the live response shape verified against the two known
tenants (Textron Aviation at textron.taleo.net and AAR Corp at
aarcorp.taleo.net):

  - Requisition rows expose visible cells under `column[]` — `column[0]`
    is the title, `column[1]` is a JSON-encoded list of locations,
    `column[2]` is the posted-date string (free-form per tenant).
  - The external job identifier is `contestNo` with `jobId` as fallback.
  - The total-count value lives under `pagingData.totalCount`.
  - The portal id appears in the `jobsearch.ftl` HTML as
    `locallogoutservlet.jss?portal=NNN&portalCode=...`.
  - The listing POST requires a `tz` header — tenants return HTTP 500
    without it.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from jobbuddy.fetchers.taleo import TaleoFetcher, _parse_date
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

FTL_HTML_LOCALLOGOUT = """\
<html>
<head><title>Textron Aviation - Job Search</title></head>
<body>
<script>
window.location.href = "/careersection/locallogoutservlet.jss?portal=8140753014&portalCode=TX";
</script>
<a href="/careersection/locallogoutservlet.jss?portal=8140753014&portalCode=TX2">Logout</a>
</body>
</html>
"""

FTL_HTML_REST_URL = """\
<html>
<head><title>Textron Aviation - Job Search</title></head>
<body>
<script>
var jobSearchUrl = "/careersection/rest/jobboard/searchjobs?lang=en&portal=2140452562";
</script>
</body>
</html>
"""

FTL_HTML_PORTAL_VAR = """\
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

DETAIL_HTML_NO_DESCRIPTION = """\
<html><body><p>Detailed job info here without proper markers</p></body></html>
"""


# ---------------------------------------------------------------------------
# Sample REST API responses (live shape)
# ---------------------------------------------------------------------------

# Textron-style date format: MM/DD/YYYY.
PAGE_1_RESPONSE = {
    "requisitionList": [
        {
            "jobId": "1536952",
            "contestNo": "341267",
            "hotJob": False,
            "alreadyAppliedOn": False,
            "column": [
                "Turning Mach Opr - 1st Shift MTC",
                "[\"US-Texas-Fort Worth\"]",
                "05/13/2026",
            ],
            "linkedColumn": 0,
            "locationsColumns": [1],
        },
        {
            "jobId": "1536953",
            "contestNo": "341268",
            "column": [
                "Aircraft Systems Engineer",
                "[\"US-Kansas-Wichita\"]",
                "04/01/2026",
            ],
        },
    ],
    "pagingData": {"totalCount": 3},
}

# AAR-style date format: "Month DD, YYYY".
PAGE_2_RESPONSE = {
    "requisitionList": [
        {
            "jobId": "1536954",
            "contestNo": "341269",
            "column": [
                "Supply Chain Analyst",
                "[\"US-Illinois-Wood Dale\"]",
                "April 22, 2026",
            ],
        },
    ],
    "pagingData": {"totalCount": 3},
}

SINGLE_PAGE_RESPONSE = {
    "requisitionList": [
        {
            "jobId": "1536952",
            "contestNo": "341267",
            "column": [
                "Aircraft Systems Engineer",
                "[\"US-Texas-Fort Worth\"]",
                "05/13/2026",
            ],
        },
    ],
    "pagingData": {"totalCount": 1},
}

# Requisition with no contestNo — must fall back to jobId.
JOB_ID_ONLY_RESPONSE = {
    "requisitionList": [
        {
            "jobId": "99999",
            "column": ["MRO Specialist", "[\"US-Texas-Dallas\"]", "03/15/2026"],
        },
    ],
    "pagingData": {"totalCount": 1},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fetcher(
    board: str = "textron",
    section: str = "textron",
    *,
    taleo_filters: dict[str, list[str]] | None = None,
) -> TaleoFetcher:
    fetcher = TaleoFetcher(
        board,
        "Textron Aviation",
        taleo_section=section,
        taleo_filters=taleo_filters,
    )
    fetcher.client = MagicMock()
    return fetcher


def _mock_get(fetcher: TaleoFetcher, html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    fetcher.client.get.return_value = resp
    return resp


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_textron_slash_date(self):
        assert _parse_date("05/13/2026") == date(2026, 5, 13)

    def test_aar_long_date(self):
        assert _parse_date("April 22, 2026") == date(2026, 4, 22)

    def test_short_month(self):
        assert _parse_date("Apr 01, 2026") == date(2026, 4, 1)

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
    def test_discovers_from_locallogoutservlet(self):
        """Live tenants expose the portal id via locallogoutservlet.jss."""
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_LOCALLOGOUT)
        assert fetcher._get_portal_id() == "8140753014"

    def test_discovers_from_rest_url(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_REST_URL)
        assert fetcher._get_portal_id() == "2140452562"

    def test_discovers_from_js_variable(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_PORTAL_VAR)
        assert fetcher._get_portal_id() == "9876543210"

    def test_raises_when_not_found(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_NO_PORTAL)
        with pytest.raises(ValueError, match="Cannot discover Taleo portal ID"):
            fetcher._get_portal_id()

    def test_portal_override_skips_discovery(self):
        fetcher = TaleoFetcher("textron", taleo_section="textron", taleo_portal="STATIC123")
        fetcher.client = MagicMock()
        assert fetcher._get_portal_id() == "STATIC123"
        fetcher.client.get.assert_not_called()

    def test_caches_portal_id(self):
        fetcher = _make_fetcher()
        _mock_get(fetcher, FTL_HTML_LOCALLOGOUT)
        fetcher._get_portal_id()
        fetcher._get_portal_id()
        assert fetcher.client.get.call_count == 1

    def test_uses_taleo_section_in_ftl_url(self):
        fetcher = _make_fetcher(board="aarcorp", section="2")
        _mock_get(fetcher, FTL_HTML_LOCALLOGOUT)
        fetcher._get_portal_id()
        called_url = fetcher.client.get.call_args[0][0]
        assert "aarcorp.taleo.net" in called_url
        assert "/careersection/2/" in called_url


# ---------------------------------------------------------------------------
# Requisition parsing — live `column[]` shape
# ---------------------------------------------------------------------------


class TestRequisitionParsing:
    def test_full_requisition(self):
        fetcher = _make_fetcher()
        item = {
            "jobId": "1536952",
            "contestNo": "341267",
            "column": [
                "Aircraft Systems Engineer",
                "[\"US-Kansas-Wichita\"]",
                "05/13/2026",
            ],
        }
        job = fetcher._parse_requisition(item)
        assert job is not None
        assert job.id == "341267"  # contestNo, not jobId
        assert job.title == "Aircraft Systems Engineer"
        assert job.location == "US-Kansas-Wichita"
        assert job.department is None  # listing payload doesn't carry job-field
        assert job.published_at == date(2026, 5, 13)
        assert "jobdetail.ftl" in job.url
        assert "job=341267" in job.url

    def test_aar_style_date_format(self):
        """AAR Corp tenant uses `Month DD, YYYY`; dateutil handles both."""
        fetcher = _make_fetcher()
        item = {
            "jobId": "1",
            "contestNo": "AAR-1",
            "column": ["Tech", "[\"US-IL-Wood Dale\"]", "April 22, 2026"],
        }
        job = fetcher._parse_requisition(item)
        assert job.published_at == date(2026, 4, 22)

    def test_falls_back_to_job_id_when_no_contest_no(self):
        fetcher = _make_fetcher()
        item = {
            "jobId": "99999",
            "column": ["MRO Specialist", "[\"US-TX-Dallas\"]", "03/15/2026"],
        }
        job = fetcher._parse_requisition(item)
        assert job is not None
        assert job.id == "99999"

    def test_multi_location_joined(self):
        fetcher = _make_fetcher()
        item = {
            "contestNo": "x",
            "column": ["Engineer", "[\"US-TX-Dallas\", \"US-KS-Wichita\"]", ""],
        }
        job = fetcher._parse_requisition(item)
        assert "US-TX-Dallas" in job.location
        assert "US-KS-Wichita" in job.location

    def test_unparseable_location_string_kept_as_is(self):
        fetcher = _make_fetcher()
        item = {
            "contestNo": "x",
            "column": ["Engineer", "Dallas, TX", ""],
        }
        job = fetcher._parse_requisition(item)
        assert job.location == "Dallas, TX"

    def test_no_id_returns_none(self):
        fetcher = _make_fetcher()
        job = fetcher._parse_requisition({"column": ["Engineer", "", ""]})
        assert job is None

    def test_missing_column_fields_tolerated(self):
        fetcher = _make_fetcher()
        item = {"contestNo": "x", "column": []}
        job = fetcher._parse_requisition(item)
        assert job is not None
        assert job.title == ""
        assert job.location == ""
        assert job.published_at is None

    def test_url_encodes_section(self):
        fetcher = _make_fetcher(board="aarcorp", section="2")
        item = {"contestNo": "123", "column": ["Test", "", ""]}
        job = fetcher._parse_requisition(item)
        assert "/careersection/2/jobdetail.ftl" in job.url


# ---------------------------------------------------------------------------
# Description extraction (listing-page-side fixtures only; live detail-page
# extraction is tracked separately — see follow-up issue.)
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
        get_resp = MagicMock()
        get_resp.text = FTL_HTML_LOCALLOGOUT
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
        assert jobs[0].id == "341267"
        assert jobs[0].title == "Aircraft Systems Engineer"
        assert jobs[0].location == "US-Texas-Fort Worth"
        assert jobs[0].published_at == date(2026, 5, 13)

    def test_pagination_uses_total_count(self):
        """Pagination loops while jobs collected < pagingData.totalCount."""
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [PAGE_1_RESPONSE, PAGE_2_RESPONSE])
        jobs = fetcher.list_jobs()
        assert len(jobs) == 3
        ids = {j.id for j in jobs}
        assert "341267" in ids
        assert "341269" in ids

    def test_rest_endpoint_includes_portal(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])
        fetcher.list_jobs()
        called_url = fetcher.client.post.call_args[0][0]
        assert "portal=8140753014" in called_url

    def test_tz_header_is_sent(self):
        """Without a `tz` header, live tenants return HTTP 500."""
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])
        fetcher.list_jobs()
        kwargs = fetcher.client.post.call_args.kwargs
        headers = kwargs.get("headers") or {}
        assert "tz" in headers
        assert headers["tz"]  # non-empty

    def test_page_body_increments(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [PAGE_1_RESPONSE, PAGE_2_RESPONSE])
        fetcher.list_jobs()
        first_body = fetcher.client.post.call_args_list[0].kwargs.get("json")
        second_body = fetcher.client.post.call_args_list[1].kwargs.get("json")
        assert first_body["pageNo"] == 1
        assert second_body["pageNo"] == 2

    def test_progress_callback(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [SINGLE_PAGE_RESPONSE])
        calls = []
        fetcher.list_jobs(on_progress=lambda f, t: calls.append((f, t)))
        assert calls[-1] == (1, 1)

    def test_falls_back_to_job_id_when_contest_missing(self):
        fetcher = _make_fetcher()
        self._setup_list(fetcher, [JOB_ID_ONLY_RESPONSE])
        jobs = fetcher.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "99999"


# ---------------------------------------------------------------------------
# Faucet config — taleo_filters
# ---------------------------------------------------------------------------


class TestTaleoFilters:
    def _setup(self, fetcher: TaleoFetcher) -> None:
        get_resp = MagicMock()
        get_resp.text = FTL_HTML_LOCALLOGOUT
        get_resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.json.return_value = SINGLE_PAGE_RESPONSE
        post_resp.raise_for_status = MagicMock()
        fetcher.client.post.return_value = post_resp

    def test_no_filters_sends_empty_buckets(self):
        """Backwards-compat: no taleo_filters → today's all-empty body."""
        fetcher = _make_fetcher()
        self._setup(fetcher)
        fetcher.list_jobs()
        body = fetcher.client.post.call_args.kwargs["json"]
        for bucket in body["filterSelectionParam"]["searchFilterSelections"]:
            assert bucket["selectedValues"] == []

    def test_job_field_filter_populates_bucket(self):
        fetcher = _make_fetcher(taleo_filters={"JOB_FIELD": ["8240753014"]})
        self._setup(fetcher)
        fetcher.list_jobs()
        body = fetcher.client.post.call_args.kwargs["json"]
        buckets = {
            b["id"]: b["selectedValues"]
            for b in body["filterSelectionParam"]["searchFilterSelections"]
        }
        assert buckets["JOB_FIELD"] == ["8240753014"]
        # Other buckets stay empty.
        assert buckets["LOCATION"] == []
        assert buckets["ORGANIZATION"] == []

    def test_multiple_filter_values(self):
        fetcher = _make_fetcher(
            taleo_filters={"JOB_FIELD": ["a", "b"], "LOCATION": ["c"]},
        )
        self._setup(fetcher)
        fetcher.list_jobs()
        body = fetcher.client.post.call_args.kwargs["json"]
        buckets = {
            b["id"]: b["selectedValues"]
            for b in body["filterSelectionParam"]["searchFilterSelections"]
        }
        assert buckets["JOB_FIELD"] == ["a", "b"]
        assert buckets["LOCATION"] == ["c"]


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

        desc = fetcher.fetch_description("341267")
        assert desc is not None
        assert "experienced engineer" in desc

    def test_uses_metadata_url(self):
        fetcher = _make_fetcher()
        resp = MagicMock()
        resp.text = DETAIL_HTML_DIV
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        custom_url = "https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=341267&lang=en"
        fetcher.fetch_description("341267", metadata={"url": custom_url})
        fetcher.client.get.assert_called_with(custom_url)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestURLParsing:
    def test_parse_textron_url(self):
        url = "https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=341267&lang=en"
        result = parse_url(url)
        assert result is not None
        assert result.ats == "taleo"
        assert result.board == "textron"
        assert result.job_id == "341267"

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
