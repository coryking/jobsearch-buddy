"""Tests for the TalentBrew fetcher's detail-page parser.

Focuses on JSON-LD extraction of description and datePosted, including the
non-zero-padded date format real Walgreens pages return (e.g. "2026-4-25").
"""

from datetime import date

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
