"""Tests for jobbuddy.models utilities."""

from datetime import date, datetime, timezone

from jobbuddy.models import parse_published_at


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
