"""Tests for parse_duration_to_date — human-friendly duration parsing."""

from datetime import date, timedelta

import pytest
from dateutil.relativedelta import relativedelta

from jobbuddy.core.durations import parse_duration_to_date


class TestExistingUnits:
    """Existing h/d/w units still work."""

    def test_hours(self):
        result = parse_duration_to_date("24h")
        assert result == (date.today() - timedelta(hours=24)).isoformat()

    def test_days(self):
        result = parse_duration_to_date("3d")
        assert result == (date.today() - timedelta(days=3)).isoformat()

    def test_weeks(self):
        result = parse_duration_to_date("2w")
        assert result == (date.today() - timedelta(weeks=2)).isoformat()


class TestMonthsAndYears:
    """New m/y units via relativedelta."""

    def test_months(self):
        result = parse_duration_to_date("6m")
        expected = (date.today() - relativedelta(months=6)).isoformat()
        assert result == expected

    def test_single_month(self):
        result = parse_duration_to_date("1m")
        expected = (date.today() - relativedelta(months=1)).isoformat()
        assert result == expected

    def test_years(self):
        result = parse_duration_to_date("1y")
        expected = (date.today() - relativedelta(years=1)).isoformat()
        assert result == expected

    def test_two_years(self):
        result = parse_duration_to_date("2y")
        expected = (date.today() - relativedelta(years=2)).isoformat()
        assert result == expected


class TestISODatePassthrough:
    """ISO YYYY-MM-DD values pass through as-is."""

    def test_iso_date(self):
        assert parse_duration_to_date("2026-01-15") == "2026-01-15"

    def test_iso_date_with_whitespace(self):
        assert parse_duration_to_date("  2026-06-01  ") == "2026-06-01"

    def test_invalid_iso_date(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("2026-13-01")

    def test_partial_date_rejected(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("2026-01")


class TestInvalidInput:
    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("")

    def test_bare_number(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("42")

    def test_unknown_unit(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("5x")

    def test_negative(self):
        with pytest.raises(ValueError):
            parse_duration_to_date("-3d")
