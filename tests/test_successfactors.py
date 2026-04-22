"""Tests for SuccessFactors ATS fetcher and URL parser."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.fetchers.successfactors import SuccessFactorsFetcher
from jobbuddy.url import parse_url, ParsedURL


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

PAGINATION_LABEL = '<span class="paginationLabel">Results <b>1 – 25</b> of <b>95</b></span>'

FULL_ROW = """\
<tr class="data-row">
    <td class="colTitle" headers="hdrTitle">
        <span class="jobTitle hidden-phone">
            <a href="/job/Renton-Automotive-Painter-WA-98055/1260533301/" class="jobTitle-link">Automotive Painter</a>
        </span>
        <div class="jobdetail-phone visible-phone">
            <span class="jobTitle visible-phone">
                <a class="jobTitle-link" href="/job/Renton-Automotive-Painter-WA-98055/1260533301/">Automotive Painter</a>
            </span>
            <span class="jobLocation visible-phone">
                <span class="jobLocation">Renton, WA, US, 98055</span>
            </span>
        </div>
    </td>
    <td class="colLocation hidden-phone" headers="hdrLocation">
        <span class="jobLocation">Renton, WA, US, 98055</span>
    </td>
    <td class="colDepartment hidden-phone" headers="hdrDepartment">
        <span class="jobDepartment">Manufacturing &amp; Distribution</span>
    </td>
    <td class="colDate hidden-phone" nowrap="nowrap" headers="hdrDate">
        <span class="jobDate">Apr 22, 2026</span>
    </td>
    <td class="colFacility hidden-phone" headers="hdrFacility">
        <span class="jobFacility">Kenworth Renton</span>
    </td>
    <td class="hidden-phone"></td>
</tr>
"""

MINIMAL_ROW = """\
<tr class="data-row">
    <td class="colTitle" headers="hdrTitle">
        <span class="jobTitle hidden-phone">
            <a href="/job/Farnborough-Aftrmkt-Expediter-%28FAR%29-ENG/1367050300/" class="jobTitle-link">Aftrmkt Expediter (FAR)</a>
        </span>
        <div class="jobdetail-phone visible-phone">
            <span class="jobTitle visible-phone">
                <a class="jobTitle-link" href="/job/Farnborough-Aftrmkt-Expediter-%28FAR%29-ENG/1367050300/">Aftrmkt Expediter (FAR)</a>
            </span>
            <span class="jobLocation visible-phone">
                <span class="jobLocation">Farnborough, ENG, GB</span>
            </span>
            <span class="jobDate visible-phone">Apr 22, 2026</span>
        </div>
    </td>
    <td class="colLocation hidden-phone" headers="hdrLocation">
        <span class="jobLocation">Farnborough, ENG, GB</span>
    </td>
    <td class="colDate hidden-phone" nowrap="nowrap" headers="hdrDate">
        <span class="jobDate">Apr 22, 2026</span>
    </td>
    <td class="hidden-phone"></td>
</tr>
"""

DETAIL_HTML = """\
<html>
<head><title>Automotive Painter - Renton, WA</title></head>
<body>
<h1 class="job-title" id="job-title">Automotive Painter</h1>
<span class="job-location" id="job-location">Renton, WA, US, 98055</span>
<div class="jdp-job-description-card">
    <div class="jobdescription">
        <p>We are looking for an <strong>Automotive Painter</strong> to join our team.</p>
        <ul>
            <li>Paint vehicles and components</li>
            <li>Prepare surfaces for painting</li>
        </ul>
        <p>Requirements: 3+ years experience.</p>
    </div>
</div>
</body>
</html>
"""


def _make_search_page(rows_html: str, total: int, start: int = 1, end: int = 25) -> str:
    """Wrap data rows in a search results page with pagination."""
    return f"""\
<html>
<body>
<span class="paginationLabel">Results <b>{start} – {end}</b> of <b>{total}</b></span>
<table>
{rows_html}
</table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Fetcher construction
# ---------------------------------------------------------------------------


def _make_fetcher(careers_url: str = "https://jobs.paccar.com") -> SuccessFactorsFetcher:
    fetcher = SuccessFactorsFetcher("paccar", "PACCAR", careers_url=careers_url)
    fetcher.client = MagicMock()
    return fetcher


# ---------------------------------------------------------------------------
# HTML parsing tests
# ---------------------------------------------------------------------------


class TestSearchParsing:
    def test_parse_full_row(self):
        """Parse a row with all columns (title, location, department, date, facility)."""
        fetcher = _make_fetcher()
        html = _make_search_page(FULL_ROW, total=1, end=1)
        jobs, total = fetcher._parse_search_html(html)

        assert total == 1
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "1260533301"
        assert job.title == "Automotive Painter"
        assert job.location == "Renton, WA, US, 98055"
        assert job.department == "Manufacturing & Distribution"
        assert job.team == "Kenworth Renton"
        assert job.published_at == date(2026, 4, 22)
        assert job.url == "https://jobs.paccar.com/job/Renton-Automotive-Painter-WA-98055/1260533301/"

    def test_parse_minimal_row(self):
        """Parse a row with only title, location, and date (no department/facility)."""
        fetcher = _make_fetcher("https://careers.gulfstream.com")
        html = _make_search_page(MINIMAL_ROW, total=1, end=1)
        jobs, total = fetcher._parse_search_html(html)

        assert total == 1
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "1367050300"
        assert job.title == "Aftrmkt Expediter (FAR)"
        assert job.location == "Farnborough, ENG, GB"
        assert job.department is None
        assert job.team is None
        assert job.published_at == date(2026, 4, 22)

    def test_parse_multiple_rows(self):
        """Parse multiple rows from a single page."""
        fetcher = _make_fetcher()
        html = _make_search_page(FULL_ROW + MINIMAL_ROW, total=2, end=2)
        jobs, total = fetcher._parse_search_html(html)

        assert total == 2
        assert len(jobs) == 2
        assert jobs[0].id == "1260533301"
        assert jobs[1].id == "1367050300"

    def test_parse_empty_page(self):
        """Parse a page with no data rows."""
        fetcher = _make_fetcher()
        html = _make_search_page("", total=0, start=0, end=0)
        jobs, total = fetcher._parse_search_html(html)

        assert total == 0
        assert len(jobs) == 0

    def test_pagination_total_extraction(self):
        """Extracts total from paginationLabel correctly."""
        fetcher = _make_fetcher()
        html = _make_search_page(FULL_ROW, total=95)
        _, total = fetcher._parse_search_html(html)
        assert total == 95

    def test_html_entity_decoded_in_department(self):
        """HTML entities (&amp;) in department are decoded."""
        fetcher = _make_fetcher()
        html = _make_search_page(FULL_ROW, total=1, end=1)
        jobs, _ = fetcher._parse_search_html(html)
        # The raw HTML has &amp; which should be decoded to &
        assert "&amp;" not in jobs[0].department
        assert "&" in jobs[0].department


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


class TestDateParsing:
    def test_standard_date(self):
        """Parse 'Apr 22, 2026' format."""
        from jobbuddy.fetchers.successfactors import _parse_date
        assert _parse_date("Apr 22, 2026") == date(2026, 4, 22)

    def test_different_months(self):
        """Parse various month abbreviations."""
        from jobbuddy.fetchers.successfactors import _parse_date
        assert _parse_date("Jan 1, 2026") == date(2026, 1, 1)
        assert _parse_date("Dec 31, 2025") == date(2025, 12, 31)

    def test_none_on_invalid(self):
        """Returns None for unparseable dates."""
        from jobbuddy.fetchers.successfactors import _parse_date
        assert _parse_date("") is None
        assert _parse_date("not a date") is None

    def test_none_on_none(self):
        """Returns None for None input."""
        from jobbuddy.fetchers.successfactors import _parse_date
        assert _parse_date(None) is None


# ---------------------------------------------------------------------------
# Job detail parsing
# ---------------------------------------------------------------------------


class TestDetailParsing:
    def test_extract_description(self):
        """Extracts description from jobdescription div."""
        fetcher = _make_fetcher()
        desc = fetcher._extract_description(DETAIL_HTML)
        assert desc is not None
        assert "Automotive Painter" in desc
        assert "Paint vehicles and components" in desc
        assert "Requirements: 3+ years experience" in desc

    def test_no_description_div(self):
        """Returns None when no jobdescription div exists."""
        fetcher = _make_fetcher()
        desc = fetcher._extract_description("<html><body>No job here</body></html>")
        assert desc is None


# ---------------------------------------------------------------------------
# list_jobs with pagination
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_single_page(self):
        """list_jobs with all results on one page."""
        fetcher = _make_fetcher()
        page_html = _make_search_page(FULL_ROW, total=1, end=1)

        resp = MagicMock()
        resp.text = page_html
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        jobs = fetcher.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "1260533301"

    def test_pagination(self):
        """list_jobs fetches multiple pages when total > 25."""
        fetcher = _make_fetcher()

        # Page 1: 1 job, total=2 (simulating 25/page but using 1 for test simplicity)
        page1_html = _make_search_page(FULL_ROW, total=2, end=1)
        page2_html = _make_search_page(MINIMAL_ROW, total=2, start=2, end=2)

        page1_resp = MagicMock()
        page1_resp.text = page1_html
        page1_resp.raise_for_status = MagicMock()

        page2_resp = MagicMock()
        page2_resp.text = page2_html
        page2_resp.raise_for_status = MagicMock()

        fetcher.client.get.side_effect = [page1_resp, page2_resp]

        # Mock _RECORDS_PER_PAGE to 1 for testing
        with patch("jobbuddy.fetchers.successfactors._RECORDS_PER_PAGE", 1):
            jobs = fetcher.list_jobs()

        assert len(jobs) == 2
        ids = {j.id for j in jobs}
        assert "1260533301" in ids
        assert "1367050300" in ids

    def test_progress_callback(self):
        """list_jobs calls on_progress with (fetched, total)."""
        fetcher = _make_fetcher()
        page_html = _make_search_page(FULL_ROW, total=1, end=1)

        resp = MagicMock()
        resp.text = page_html
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        progress_calls = []
        fetcher.list_jobs(on_progress=lambda f, t: progress_calls.append((f, t)))

        assert len(progress_calls) >= 1
        assert progress_calls[0] == (1, 1)


# ---------------------------------------------------------------------------
# fetch_description
# ---------------------------------------------------------------------------


class TestFetchDescription:
    def test_fetch_description(self):
        """fetch_description GETs the detail page and extracts description."""
        fetcher = _make_fetcher()

        resp = MagicMock()
        resp.text = DETAIL_HTML
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        desc = fetcher.fetch_description("1260533301", metadata={"url": "https://jobs.paccar.com/job/Renton-Automotive-Painter-WA-98055/1260533301/"})
        assert desc is not None
        assert "Automotive Painter" in desc

    def test_fetch_description_uses_metadata_url(self):
        """fetch_description uses the URL from metadata."""
        fetcher = _make_fetcher()

        resp = MagicMock()
        resp.text = DETAIL_HTML
        resp.raise_for_status = MagicMock()
        fetcher.client.get.return_value = resp

        fetcher.fetch_description("1260533301", metadata={"url": "https://jobs.paccar.com/job/slug/1260533301/"})
        fetcher.client.get.assert_called_with("https://jobs.paccar.com/job/slug/1260533301/")


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestURLParsing:
    @patch("jobbuddy.fetchers.successfactors.load_registry")
    def test_parse_paccar_url(self, mock_registry):
        """Parse a PACCAR SuccessFactors job URL."""
        from jobbuddy.models import Company

        mock_registry.return_value = {
            "paccar": Company(
                slug="paccar",
                name="PACCAR",
                ats="successfactors",
                board="paccar",
                careers_url="https://jobs.paccar.com",
            ),
        }

        result = parse_url("https://jobs.paccar.com/job/Renton-Automotive-Painter-WA-98055/1260533301/")
        assert result is not None
        assert result.ats == "successfactors"
        assert result.board == "paccar"
        assert result.job_id == "1260533301"

    @patch("jobbuddy.fetchers.successfactors.load_registry")
    def test_parse_gulfstream_url(self, mock_registry):
        """Parse a Gulfstream SuccessFactors job URL."""
        from jobbuddy.models import Company

        mock_registry.return_value = {
            "gulfstream": Company(
                slug="gulfstream",
                name="Gulfstream",
                ats="successfactors",
                board="gulfstream",
                careers_url="https://careers.gulfstream.com",
            ),
        }

        result = parse_url("https://careers.gulfstream.com/job/Farnborough-Aftrmkt-Expediter-%28FAR%29-ENG/1367050300/")
        assert result is not None
        assert result.ats == "successfactors"
        assert result.board == "gulfstream"
        assert result.job_id == "1367050300"

    @patch("jobbuddy.fetchers.successfactors.load_registry")
    def test_no_match_for_unknown_host(self, mock_registry):
        """Returns None for a host not in the registry."""
        from jobbuddy.models import Company

        mock_registry.return_value = {
            "paccar": Company(
                slug="paccar",
                name="PACCAR",
                ats="successfactors",
                board="paccar",
                careers_url="https://jobs.paccar.com",
            ),
        }

        result = parse_url("https://unknown-company.com/job/some-slug/12345/")
        assert result is None

    def test_non_job_url_not_matched(self):
        """URLs without /job/{slug}/{numericId} are not matched."""
        result = parse_url("https://jobs.paccar.com/search-jobs/results")
        assert result is None
