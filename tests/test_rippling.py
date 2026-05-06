"""Tests for the Rippling ATS fetcher's stub-fetcher behavior.

Rippling's list endpoint returns only {department, name, url, uuid, workLocation}
— no description, no createdOn, no payRangeDetails. Those live only on the
detail endpoint, so Rippling must behave as a stub fetcher (enriched after
listing) rather than a full one.
"""

from datetime import date

from jobbuddy.fetchers.rippling import RipplingFetcher, _parse_created_on


class TestStubFetcherShape:
    def test_descriptions_not_in_listing(self):
        """Rippling cannot deliver descriptions from list_jobs(), so it must
        be flagged as a stub fetcher; otherwise the enrich phase skips it
        and descriptions stay NULL forever."""
        assert RipplingFetcher.descriptions_in_listing is False

    def test_enrichment_fills_includes_published_at(self):
        """Detail responses carry createdOn — declare published_at as
        enrichable so existing rows missing a posted date get re-fetched."""
        assert "description" in RipplingFetcher.enrichment_fills
        assert "published_at" in RipplingFetcher.enrichment_fills


class TestParseDetailJob:
    """The _parse_job helper must extract description, published_at, and
    salary correctly from a detail response."""

    def _detail_payload(self) -> dict:
        return {
            "uuid": "abc-123",
            "name": "Staff Engineer",
            "url": "https://ats.rippling.com/rippling/jobs/abc-123",
            "department": {"label": "Engineering"},
            "workLocations": [{"label": "Remote"}],
            "description": {
                "role": "<p>Build cool things.</p>",
                "company": "<p>We are a company.</p>",
            },
            "payRangeDetails": {"min": 200000, "max": 300000},
            "createdOn": "2026-03-03T22:14:19.782000-08:00",
        }

    def test_extracts_published_at(self):
        f = RipplingFetcher(board="rippling")
        job = f._parse_job(self._detail_payload())
        assert job.published_at == date(2026, 3, 3)

    def test_extracts_description(self):
        f = RipplingFetcher(board="rippling")
        job = f._parse_job(self._detail_payload())
        assert job.description is not None
        assert "Build cool things" in job.description

    def test_extracts_salary(self):
        f = RipplingFetcher(board="rippling")
        job = f._parse_job(self._detail_payload())
        assert job.salary == "200000 - 300000"


class TestParseCreatedOn:
    """Rippling's createdOn is a full ISO timestamp; we keep only the date.
    A non-ISO value must not crash _parse_job — it returns None and logs."""

    def test_parses_iso_timestamp(self):
        assert _parse_created_on("2026-03-03T22:14:19.782000-08:00") == date(2026, 3, 3)

    def test_returns_none_on_empty(self):
        assert _parse_created_on(None) is None
        assert _parse_created_on("") is None

    def test_returns_none_on_garbage(self):
        """Source format change or corruption — don't propagate as ValidationError."""
        assert _parse_created_on("not-a-date") is None
        assert _parse_created_on("3/3/2026") is None


class TestParseListStub:
    """The same _parse_job called on list-shape data must produce a usable
    stub — no description / published_at / salary, but id/title/url/location
    intact."""

    def test_list_shape_yields_stub(self):
        f = RipplingFetcher(board="rippling")
        list_payload = {
            "uuid": "abc-123",
            "name": "Staff Engineer",
            "url": "https://ats.rippling.com/rippling/jobs/abc-123",
            "department": {"label": "Engineering"},
            "workLocation": {"label": "Remote"},
        }
        job = f._parse_job(list_payload)
        assert job.id == "abc-123"
        assert job.title == "Staff Engineer"
        assert job.location == "Remote"
        assert job.description is None
        assert job.published_at is None
        assert job.salary is None
