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

    def test_fetch_description_still_returns_string(self):
        """The legacy str-only fetch_description path must keep working —
        it's what the current enrich phase calls."""
        fetcher = _make_fetcher()
        html = _wrap_jsonld(
            '{"@type":"JobPosting","description":"Just the desc","datePosted":"2026-1-2"}'
        )
        # _extract_description_from_html is the legacy entry point used by
        # fetch_description.
        assert fetcher._extract_description_from_html(html) == "Just the desc"


class TestEnrichmentFills:
    def test_declares_published_at(self):
        """TalentBrew must declare it fills published_at so the enrich phase
        re-fetches rows missing a posted date, not just rows missing a
        description."""
        assert "published_at" in TalentBrewFetcher.enrichment_fills
        assert "description" in TalentBrewFetcher.enrichment_fills
