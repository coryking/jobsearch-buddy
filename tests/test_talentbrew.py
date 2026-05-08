"""Tests for the TalentBrew fetcher's detail-page parser and list_jobs filtering.

Covers JSON-LD extraction, per-company ACM filter config, static pagination,
and keyword-pass deduplication.
"""

from datetime import date
from unittest.mock import MagicMock, patch

from jobbuddy.fetchers.talentbrew import TalentBrewFetcher


def _make_fetcher() -> TalentBrewFetcher:
    return TalentBrewFetcher(
        board="walgreens",
        tb_host="jobs.walgreens.com",
        tb_tenant_id=1242,
    )


def _wrap_jsonld(payload: str) -> str:
    return (
        f'<html><head>'
        f'<script type="application/ld+json">{payload}</script>'
        f'</head><body></body></html>'
    )


class TestParseDetailPage:
    def test_extracts_description_and_padded_date(self):
        fetcher = _make_fetcher()
        html = _wrap_jsonld(
            '{"@type":"JobPosting","description":"Hello world",'
            '"datePosted":"2026-04-25"}'
        )
        result = fetcher._parse_detail_page(html)
        assert result["description"] == "Hello world"
        assert result["published_at"] == date(2026, 4, 25)

    def test_handles_non_zero_padded_date(self):
        """Walgreens (and likely other TalentBrew tenants) emit datePosted as
        '2026-4-25' rather than '2026-04-25'. date.fromisoformat() rejects this,
        so the parser must handle it explicitly."""
        fetcher = _make_fetcher()
        html = _wrap_jsonld(
            '{"@type":"JobPosting","description":"Hello",'
            '"datePosted":"2026-4-25"}'
        )
        result = fetcher._parse_detail_page(html)
        assert result["published_at"] == date(2026, 4, 25)

    def test_missing_dateposted_is_none(self):
        fetcher = _make_fetcher()
        html = _wrap_jsonld('{"@type":"JobPosting","description":"Hello"}')
        result = fetcher._parse_detail_page(html)
        assert result["description"] == "Hello"
        assert result["published_at"] is None

    def test_no_jsonld_returns_empty(self):
        fetcher = _make_fetcher()
        html = "<html><body>no jsonld here</body></html>"
        result = fetcher._parse_detail_page(html)
        assert result["description"] is None
        assert result["published_at"] is None

    def test_malformed_jsonld_skipped(self):
        fetcher = _make_fetcher()
        html = (
            '<script type="application/ld+json">{not json}</script>'
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","description":"Real one","datePosted":"2026-3-1"}'
            '</script>'
        )
        result = fetcher._parse_detail_page(html)
        assert result["description"] == "Real one"
        assert result["published_at"] == date(2026, 3, 1)

    def test_invalid_dateposted_is_none(self):
        """A garbage datePosted shouldn't crash — we just give up the date."""
        fetcher = _make_fetcher()
        html = _wrap_jsonld(
            '{"@type":"JobPosting","description":"Hi","datePosted":"not-a-date"}'
        )
        result = fetcher._parse_detail_page(html)
        assert result["description"] == "Hi"
        assert result["published_at"] is None

    def test_calendar_invalid_dateposted_is_none(self):
        """A regex-matching but calendar-invalid date (month 13, day 0,
        Feb 30) must not raise — _parse_loose_date catches the ValueError
        from date() construction."""
        fetcher = _make_fetcher()
        for bad in ("2026-13-01", "2026-00-15", "2026-02-30"):
            html = _wrap_jsonld(
                f'{{"@type":"JobPosting","description":"Hi","datePosted":"{bad}"}}'
            )
            result = fetcher._parse_detail_page(html)
            assert result["published_at"] is None, f"expected None for {bad}"

    def test_non_string_description_does_not_crash(self):
        """A JSON-LD block with description as a list or int (seen in some
        tenant variants) must not raise on strip_html — return None for
        description instead."""
        fetcher = _make_fetcher()
        for bad_desc in ('["a","b"]', '42', '{"nested":"obj"}'):
            html = _wrap_jsonld(
                f'{{"@type":"JobPosting","description":{bad_desc},"datePosted":"2026-3-1"}}'
            )
            result = fetcher._parse_detail_page(html)
            assert result["description"] is None, f"expected None for {bad_desc}"
            # The date should still come through.
            assert result["published_at"] == date(2026, 3, 1)

    def test_non_string_dateposted_does_not_crash(self):
        """datePosted as a non-string (number, list) must not crash."""
        fetcher = _make_fetcher()
        for bad_date in ('20260301', '["2026-3-1"]', 'null'):
            html = _wrap_jsonld(
                f'{{"@type":"JobPosting","description":"Hi","datePosted":{bad_date}}}'
            )
            result = fetcher._parse_detail_page(html)
            assert result["published_at"] is None, f"expected None for {bad_date}"

    def test_parse_returns_dict_with_both_keys_present(self):
        """The dict shape is the contract — callers may read either key,
        so both must always be present (None when absent)."""
        fetcher = _make_fetcher()
        html = _wrap_jsonld(
            '{"@type":"JobPosting","description":"Hi","datePosted":"2026-1-2"}'
        )
        result = fetcher._parse_detail_page(html)
        assert set(result.keys()) == {"description", "published_at"}


class TestEnrichmentFills:
    def test_declares_published_at(self):
        """TalentBrew must declare it fills published_at so the enrich phase
        re-fetches rows missing a posted date, not just rows missing a
        description."""
        assert "published_at" in TalentBrewFetcher.enrichment_fills
        assert "description" in TalentBrewFetcher.enrichment_fills


# ---------------------------------------------------------------------------
# Helpers for list_jobs filtering tests
# ---------------------------------------------------------------------------

def _dynamic_html_fragment(job_id: str, title: str, total: int) -> str:
    """Minimal HTML fragment for dynamic (XHR JSON) mode."""
    return (
        f'<div data-total-results="{total}">'
        f'<ul><li><a href="/en/job/test-{job_id}" data-job-id="{job_id}" '
        f'data-title="{title}"></a></li></ul></div>'
    )


def _static_html_page(job_id: str, title: str, total: int) -> str:
    """Minimal full HTML for static pagination mode."""
    return (
        f'<html><body data-total-results="{total}">'
        f'<ul><li><a href="/en/job/test-{job_id}" data-job-id="{job_id}" '
        f'data-title="{title}"></a></li></ul></body></html>'
    )


def _mock_dynamic_response(html_fragment: str) -> MagicMock:
    """Mock httpx response for dynamic (XHR JSON) mode: .json()['results'] = html."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"results": html_fragment}
    resp.text = html_fragment
    return resp


def _mock_static_response(html: str) -> MagicMock:
    """Mock httpx response for static pagination mode: .text = full html."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("static mode does not return JSON")
    resp.text = html
    return resp


class TestListJobsFiltering:
    def test_default_no_filter_uses_results_endpoint(self):
        """When tb_search_path is empty, URL hits /search-jobs/results without acm param."""
        fetcher = TalentBrewFetcher(
            board="walgreens",
            tb_host="jobs.walgreens.com",
            tb_tenant_id=1242,
            tb_search_path="",
        )
        html = _dynamic_html_fragment("100", "Engineer", 1)
        mock_resp = _mock_dynamic_response(html)

        captured_urls = []

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch.object(fetcher.client, "get", side_effect=fake_get):
            jobs = fetcher.list_jobs()

        assert len(jobs) == 1
        assert jobs[0].id == "100"
        assert all("/search-jobs/results" in u for u in captured_urls)
        assert not any("acm" in u for u in captured_urls)

    def test_dynamic_acm_filter_in_url(self):
        """When tb_search_path has acm param, URL includes acm=12899."""
        fetcher = TalentBrewFetcher(
            board="walgreens",
            tb_host="jobs.walgreens.com",
            tb_tenant_id=1242,
            tb_search_path="/en/search-jobs?acm=12899",
        )
        html = _dynamic_html_fragment("200", "Pharmacist", 1)
        mock_resp = _mock_dynamic_response(html)

        captured_urls = []

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch.object(fetcher.client, "get", side_effect=fake_get):
            jobs = fetcher.list_jobs()

        assert len(jobs) == 1
        assert any("acm=12899" in u for u in captured_urls)
        assert all("/search-jobs/results" in u for u in captured_urls)

    def test_static_pagination_builds_correct_url(self):
        """When tb_static_pagination=True, page 1 URL is /search-jobs/185/1?acm=11125."""
        fetcher = TalentBrewFetcher(
            board="boeing",
            tb_host="jobs.boeing.com",
            tb_tenant_id=185,
            tb_search_path="/search-jobs/185/1?acm=11125",
            tb_static_pagination=True,
        )
        html = _static_html_page("300", "Engineer", 1)
        mock_resp = _mock_static_response(html)

        captured_urls = []

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch.object(fetcher.client, "get", side_effect=fake_get):
            jobs = fetcher.list_jobs()

        assert len(jobs) == 1
        assert any("/search-jobs/185/1" in u for u in captured_urls)
        assert any("acm=11125" in u for u in captured_urls)
        # Must NOT use the dynamic /search-jobs/results endpoint
        assert not any("/search-jobs/results" in u for u in captured_urls)

    def test_keyword_passes_merged_and_deduped(self):
        """Keyword passes merge into main list, deduplicated by job.id."""
        fetcher = TalentBrewFetcher(
            board="boeing",
            tb_host="jobs.boeing.com",
            tb_tenant_id=185,
            tb_search_path="/search-jobs/185/1?acm=11125",
            tb_static_pagination=True,
            tb_keyword_passes=["paint"],
        )
        # Main pass: jobs 301, 302 (total=2, fits one page)
        main_html = (
            '<div data-total-results="2">'
            '<ul>'
            '<li><a href="/en/job/test-301" data-job-id="301" data-title="Job A"></a></li>'
            '<li><a href="/en/job/test-302" data-job-id="302" data-title="Job B"></a></li>'
            '</ul></div>'
        )
        # Keyword pass: jobs 302 (overlap), 303 (new)
        kw_html = (
            '<div data-total-results="2">'
            '<ul>'
            '<li><a href="/en/job/test-302" data-job-id="302" data-title="Job B"></a></li>'
            '<li><a href="/en/job/test-303" data-job-id="303" data-title="Job C"></a></li>'
            '</ul></div>'
        )

        call_count = [0]

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.side_effect = ValueError("static mode")
            call_count[0] += 1
            # First call = main pass page 1; second call = keyword pass page 1
            if call_count[0] == 1:
                resp.text = main_html
            else:
                resp.text = kw_html
            return resp

        with patch.object(fetcher.client, "get", side_effect=fake_get):
            jobs = fetcher.list_jobs()

        job_ids = {j.id for j in jobs}
        assert job_ids == {"301", "302", "303"}
        assert len(jobs) == 3
