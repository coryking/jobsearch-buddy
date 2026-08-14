"""Tests for the live board-health probe (`jobbuddy.core.board_health`).

The probe hits the same `fetcher.list_jobs()` call the live MCP surface uses,
so a board that fails here fails for the human too. These tests mock the
fetcher layer; no network.
"""

import httpx
import pytest
from unittest.mock import MagicMock, patch

from jobbuddy.core.board_health import BoardCheck, check_boards, classify_error
from jobbuddy.models import Company, Job

PATCH_TARGET = "jobbuddy.core.board_health.get_fetcher"


def _company(slug: str, ats: str = "greenhouse", board: str | None = None) -> Company:
    return Company(slug=slug, name=slug.title(), ats=ats, board=board or slug)


def _job(i: str) -> Job:
    return Job(
        id=i,
        title=f"Job {i}",
        location="Remote",
        url=f"https://example.test/{i}",
        apply_url=f"https://example.test/{i}/apply",
    )


def _fetcher(jobs=None, raises: Exception | None = None) -> MagicMock:
    f = MagicMock()
    if raises is not None:
        f.list_jobs.side_effect = raises
    else:
        f.list_jobs.return_value = jobs or []
    f.__enter__.return_value = f
    f.__exit__.return_value = False
    return f


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/board")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url 'https://example.test/board'",
        request=request,
        response=response,
    )


def _run(companies, fetcher_for) -> dict[str, BoardCheck]:
    with patch(PATCH_TARGET, side_effect=lambda c: fetcher_for(c)):
        return {r.slug: r for r in check_boards(companies, workers=2)}


def test_board_with_jobs_is_ok():
    results = _run([_company("acme")], lambda c: _fetcher([_job("1"), _job("2")]))
    assert results["acme"].status == "ok"
    assert results["acme"].total == 2
    assert results["acme"].error is None


def test_board_returning_zero_jobs_is_empty_not_ok():
    """200-with-[] is the silent drift class — a 404-only check calls it healthy."""
    results = _run([_company("ghost")], lambda c: _fetcher([]))
    assert results["ghost"].status == "empty"
    assert results["ghost"].total == 0
    assert results["ghost"].error is None


def test_board_raising_is_error_with_message():
    results = _run([_company("gone")], lambda c: _fetcher(raises=_http_error(404)))
    check = results["gone"]
    assert check.status == "error"
    assert check.total is None
    assert "404" in check.error
    assert check.error_class == "404"


def test_every_company_gets_exactly_one_result():
    companies = [_company("a"), _company("b"), _company("c")]

    def fetcher_for(c):
        return _fetcher(raises=_http_error(403)) if c.slug == "b" else _fetcher([_job("1")])

    results = _run(companies, fetcher_for)
    assert set(results) == {"a", "b", "c"}
    assert results["b"].status == "error"
    assert results["a"].status == "ok"


def test_one_failing_board_does_not_abort_the_sweep():
    """A raising fetcher must not poison the other probes — the whole point
    is enumerating every failure in one pass."""
    companies = [_company("boom"), _company("fine")]

    def fetcher_for(c):
        return _fetcher(raises=RuntimeError("kaboom")) if c.slug == "boom" else _fetcher([_job("1")])

    results = _run(companies, fetcher_for)
    assert results["boom"].status == "error"
    assert "kaboom" in results["boom"].error
    assert results["fine"].status == "ok"


def test_results_carry_registry_config_for_repair():
    """The report exists so the operator can fix the row — it has to say
    which ats/board is wrong."""
    results = _run([_company("drift", ats="ashby", board="drift-co")], lambda c: _fetcher([]))
    check = results["drift"]
    assert check.ats == "ashby"
    assert check.board == "drift-co"


def test_unsupported_ats_is_skipped_not_probed():
    """Directory-only entries (ats=None, used by activity-log backfill) have
    no board to check and must not be reported as failures."""
    directory_only = Company(slug="dir-only", name="Dir Only", ats=None)
    with patch(PATCH_TARGET, side_effect=AssertionError("must not probe")):
        results = list(check_boards([directory_only], workers=1))
    assert results == []


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_http_error(404), "404"),
        (_http_error(403), "403"),
        (_http_error(500), "500"),
        (httpx.ReadTimeout("timed out"), "timeout"),
        (ValueError("Expecting value: line 1 column 1 (char 0)"), "parse"),
        (RuntimeError("something else"), "other"),
    ],
)
def test_classify_error_buckets_by_failure_shape(exc, expected):
    assert classify_error(exc) == expected


def test_classify_error_recognizes_model_validation():
    """`1 validation error for Job` is a fetcher bug, not board drift — it
    has to bucket separately or it hides inside 'other'."""
    pydantic_error = ValueError(
        "1 validation error for Job\nlocation\n  Input should be a valid string"
    )
    assert classify_error(pydantic_error) == "validation"
