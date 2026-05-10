"""Tests for the Eightfold v2 fetcher's date extraction.

`t_create` and `t_update` are both epoch-second fields on every position.
We map them to `published_at` and `last_listing_update` respectively.
"""

from datetime import date

from jobbuddy.fetchers.eightfold_v2 import EightfoldV2Fetcher


def _make_fetcher() -> EightfoldV2Fetcher:
    return EightfoldV2Fetcher(
        board="acme",
        base_url="https://acme.eightfold.ai",
        domain="acme.com",
    )


def test_position_to_job_extracts_both_dates():
    f = _make_fetcher()
    pos = {
        "id": 12345,
        "name": "SWE",
        "t_create": 1700000000,  # 2023-11-14
        "t_update": 1776000000,  # 2026-04-12
    }
    job = f._position_to_job(pos)
    assert job.published_at == date(2023, 11, 14)
    assert job.last_listing_update == date(2026, 4, 12)


def test_position_with_no_t_update_leaves_null():
    f = _make_fetcher()
    pos = {"id": 1, "name": "SWE", "t_create": 1776000000}
    job = f._position_to_job(pos)
    assert job.last_listing_update is None
    assert job.published_at == date(2026, 4, 12)


def test_position_with_no_dates_at_all_is_null():
    f = _make_fetcher()
    job = f._position_to_job({"id": 1, "name": "SWE"})
    assert job.published_at is None
    assert job.last_listing_update is None
