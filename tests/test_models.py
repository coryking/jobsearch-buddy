"""Tests for jobbuddy.models utilities."""

from datetime import date, datetime, timezone

from jobbuddy.models import Job, parse_published_at


class TestJobLocation:
    """An ATS that emits an explicit ``"location": null`` must not take down
    the whole company's sync. ``dict.get("location", "")`` defends a *missing*
    key but returns None on an explicit null, so the value reaches Job; the
    model is the single chokepoint that has to tolerate it. Coerce None to ""
    — the already-handled no-location representation — rather than raising."""

    BASE = dict(id="1", title="Engineer", url="u", apply_url="a")

    def test_explicit_none_location_coerced_to_empty(self):
        assert Job(location=None, **self.BASE).location == ""

    def test_real_location_preserved(self):
        assert Job(location="Remote, US", **self.BASE).location == "Remote, US"


class TestParsePublishedAt:
    """parse_published_at is the shared coercion point for ATS-supplied
    date-ish values. Five fetchers depend on it; bad behavior here ripples."""

    def test_none_passthrough(self):
        assert parse_published_at(None) is None

    def test_date_passthrough(self):
        d = date(2026, 3, 4)
        assert parse_published_at(d) == d

    def test_datetime_truncates_to_date(self):
        dt = datetime(2026, 3, 4, 18, 30, tzinfo=timezone.utc)
        assert parse_published_at(dt) == date(2026, 3, 4)

    def test_iso_date_string(self):
        assert parse_published_at("2026-03-04") == date(2026, 3, 4)

    def test_iso_timestamp_string_truncates(self):
        assert parse_published_at("2026-03-04T18:30:00Z") == date(2026, 3, 4)

    def test_epoch_seconds(self):
        # 2026-04-24 ~ midday UTC
        assert parse_published_at(1777024853) == date(2026, 4, 24)

    def test_epoch_float(self):
        assert parse_published_at(1777024853.467) == date(2026, 4, 24)

    def test_garbage_string_returns_none(self):
        assert parse_published_at("not a date") is None

    def test_empty_string_returns_none(self):
        assert parse_published_at("") is None

    def test_short_year_only_returns_none(self):
        # Old paylocity _parse_date passed "2026" through; new contract:
        # anything not a full YYYY-MM-DD prefix is None. Junk-as-valid
        # is worse than missing.
        assert parse_published_at("2026") is None
