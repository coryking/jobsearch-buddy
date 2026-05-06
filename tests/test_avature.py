"""Tests for Avature sitemap-based published_at extraction.

Avature detail pages are JS-rendered with no JSON-LD or static date fields.
The careers sitemap.xml carries per-JobDetail <lastmod> values, which we use
as the posted date during list_jobs().
"""

from datetime import date

from jobbuddy.fetchers.avature import AvatureFetcher


def _make_fetcher(section: str = "careers", locale: str = "") -> AvatureFetcher:
    return AvatureFetcher(
        board="bloomberg",
        av_section=section,
        av_locale=locale,
    )


SAMPLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://bloomberg.avature.net/careers/AgentCreate</loc>
    <lastmod>2025-10-14</lastmod>
  </url>
  <url>
    <loc>https://bloomberg.avature.net/careers/JobDetail/Senior-Software-Engineer/19542</loc>
    <lastmod>2026-05-06</lastmod>
  </url>
  <url>
    <loc>https://bloomberg.avature.net/careers/JobDetail/Index-Production-Analyst/19536</loc>
    <lastmod>2026-04-15</lastmod>
  </url>
  <url>
    <loc>https://bloomberg.avature.net/careers/SearchJobs</loc>
    <lastmod>2026-05-06</lastmod>
  </url>
</urlset>
"""


class TestParseSitemap:
    def test_extracts_jobdetail_lastmods(self):
        fetcher = _make_fetcher()
        result = fetcher._parse_sitemap_dates(SAMPLE_SITEMAP)
        assert result == {
            "19542": date(2026, 5, 6),
            "19536": date(2026, 4, 15),
        }

    def test_ignores_non_jobdetail_urls(self):
        """AgentCreate, SearchJobs etc. share the sitemap; only JobDetail
        entries should be picked up."""
        fetcher = _make_fetcher()
        result = fetcher._parse_sitemap_dates(SAMPLE_SITEMAP)
        # AgentCreate has lastmod 2025-10-14 — must not show up keyed under
        # any JobDetail id.
        for v in result.values():
            assert v != date(2025, 10, 14)

    def test_empty_xml_returns_empty(self):
        fetcher = _make_fetcher()
        assert fetcher._parse_sitemap_dates("<urlset></urlset>") == {}

    def test_garbage_returns_empty(self):
        """Bad XML shouldn't raise — sitemap is best-effort. Worst case,
        published_at stays None and the scrape-date fallback covers it."""
        fetcher = _make_fetcher()
        assert fetcher._parse_sitemap_dates("not xml at all") == {}

    def test_handles_locale_prefixed_urls(self):
        """Some Avature tenants use /{locale}/{section}/JobDetail/.../{id}."""
        fetcher = _make_fetcher(section="main", locale="en_US")
        sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://bloomberg.avature.net/en_US/main/JobDetail/Some-Role/777</loc>
    <lastmod>2026-02-01</lastmod>
  </url>
</urlset>"""
        assert fetcher._parse_sitemap_dates(sitemap) == {"777": date(2026, 2, 1)}


class TestEnrichmentFills:
    def test_only_description(self):
        """Avature pulls dates from the sitemap during list_jobs(), not
        during enrich, so it does NOT declare published_at as
        enrichment-fillable."""
        assert AvatureFetcher.enrichment_fills == ("description",)
