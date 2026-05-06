"""Tests for fetcher base class retry logic and Eightfold page retries."""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from jobbuddy.fetchers.base import ATSFetcher, RetryCallback


# ---------------------------------------------------------------------------
# Concrete stub for testing base class methods
# ---------------------------------------------------------------------------


class StubFetcher(ATSFetcher):
    ats_type = "stub"
    max_retries = 3
    backoff_base = 0.01  # fast for tests

    def __init__(self):
        super().__init__("test-board", "Test")
        # Replace client with mock to avoid real HTTP
        self.client = MagicMock()

    def list_jobs(self, *, on_progress=None, on_retry=None):
        return []

    def fetch_job(self, job_id):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# _retry_request tests
# ---------------------------------------------------------------------------


class TestRetryRequest:
    def test_success_no_retry(self):
        """Succeeds on first try — no retries."""
        fetcher = StubFetcher()
        fn = MagicMock(return_value="ok")
        result = fetcher._retry_request(fn)
        assert result == "ok"
        assert fn.call_count == 1

    def test_retry_on_429(self):
        """Retries on 429 and succeeds on second attempt."""
        fetcher = StubFetcher()

        response_429 = MagicMock()
        response_429.status_code = 429
        response_429.headers = {}
        err = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=response_429)

        fn = MagicMock(side_effect=[err, "ok"])
        result = fetcher._retry_request(fn)
        assert result == "ok"
        assert fn.call_count == 2

    def test_retry_respects_retry_after_header(self):
        """Uses Retry-After header value for wait time."""
        fetcher = StubFetcher()

        response_429 = MagicMock()
        response_429.status_code = 429
        response_429.headers = {"Retry-After": "0.01"}
        err = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=response_429)

        fn = MagicMock(side_effect=[err, "ok"])
        on_retry = MagicMock()

        fetcher._retry_request(fn, on_retry=on_retry)

        # Verify the wait time passed to on_retry was from Retry-After
        assert on_retry.call_count == 1
        _, _, wait, _ = on_retry.call_args[0]
        assert wait == pytest.approx(0.01)

    def test_retry_on_read_timeout(self):
        """Retries on ReadTimeout."""
        fetcher = StubFetcher()

        err = httpx.ReadTimeout("timed out")
        fn = MagicMock(side_effect=[err, "ok"])
        result = fetcher._retry_request(fn)
        assert result == "ok"
        assert fn.call_count == 2

    def test_retry_on_connect_error(self):
        """Retries on ConnectError."""
        fetcher = StubFetcher()

        err = httpx.ConnectError("connection refused")
        fn = MagicMock(side_effect=[err, "ok"])
        result = fetcher._retry_request(fn)
        assert result == "ok"

    def test_exhausts_max_retries_raises(self):
        """Raises after max_retries exhausted."""
        fetcher = StubFetcher()
        fetcher.max_retries = 2

        err = httpx.ReadTimeout("timed out")
        fn = MagicMock(side_effect=err)

        with pytest.raises(httpx.ReadTimeout):
            fetcher._retry_request(fn)

        # 1 initial + 2 retries = 3 calls
        assert fn.call_count == 3

    def test_non_429_http_error_not_retried(self):
        """Non-429 HTTP errors are raised immediately."""
        fetcher = StubFetcher()

        response_500 = MagicMock()
        response_500.status_code = 500
        err = httpx.HTTPStatusError("server error", request=MagicMock(), response=response_500)

        fn = MagicMock(side_effect=err)
        with pytest.raises(httpx.HTTPStatusError):
            fetcher._retry_request(fn)
        assert fn.call_count == 1

    def test_on_retry_callback_args(self):
        """on_retry is called with (attempt, max_attempts, wait, reason)."""
        fetcher = StubFetcher()
        fetcher.max_retries = 2

        err = httpx.ReadTimeout("timed out")
        fn = MagicMock(side_effect=[err, "ok"])
        on_retry = MagicMock()

        fetcher._retry_request(fn, on_retry=on_retry)

        assert on_retry.call_count == 1
        attempt, max_attempts, wait, reason = on_retry.call_args[0]
        assert attempt == 1
        assert max_attempts == 2
        assert wait > 0
        assert "timed out" in reason

    def test_arbitrary_exception_not_retried(self):
        """Non-transient exceptions (ValueError, etc.) are not retried."""
        fetcher = StubFetcher()
        fn = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError):
            fetcher._retry_request(fn)
        assert fn.call_count == 1


# ---------------------------------------------------------------------------
# fetch_enrichments retry integration
# ---------------------------------------------------------------------------


class TestFetchEnrichmentsRetry:
    def test_fetch_enrichments_retries_429(self):
        """fetch_enrichments uses _retry_request for 429 retries."""
        fetcher = StubFetcher()
        fetcher.enrich_delay = 0

        response_429 = MagicMock()
        response_429.status_code = 429
        response_429.headers = {}
        err = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=response_429)

        fetcher.fetch_enrichment = MagicMock(side_effect=[err, {"description": "description"}])

        on_retry = MagicMock()
        results = fetcher.fetch_enrichments(["j1"], on_retry=on_retry)

        assert results["j1"] == {"description": "description"}
        assert on_retry.call_count == 1

    def test_fetch_enrichments_calls_on_retry(self):
        """on_retry callback is called during fetch_enrichments retries."""
        fetcher = StubFetcher()
        fetcher.enrich_delay = 0

        err = httpx.ReadTimeout("timeout")
        fetcher.fetch_enrichment = MagicMock(side_effect=[err, {"description": "desc"}])

        retry_events = []
        results = fetcher.fetch_enrichments(
            ["j1"],
            on_retry=lambda a, ma, w, r: retry_events.append((a, ma, w, r)),
        )
        assert results["j1"] == {"description": "desc"}
        assert len(retry_events) == 1
        assert retry_events[0][0] == 1  # attempt

    def test_fetch_enrichments_404_calls_on_gone_and_logs_info(self, caplog):
        """A 404 from the ATS detail page means the listing is gone.
        fetch_enrichments must call on_gone(job_id), skip on_fetched, and
        log at INFO (not WARNING) — these are expected, not errors."""
        import logging
        fetcher = StubFetcher()
        fetcher.enrich_delay = 0

        response_404 = MagicMock()
        response_404.status_code = 404
        err = httpx.HTTPStatusError("not found", request=MagicMock(), response=response_404)
        fetcher.fetch_enrichment = MagicMock(side_effect=err)

        gone = []
        fetched = []
        with caplog.at_level(logging.INFO, logger="jobbuddy.fetchers.base"):
            fetcher.fetch_enrichments(
                ["j1"],
                on_fetched=lambda jid, p: fetched.append((jid, p)),
                on_gone=lambda jid: gone.append(jid),
            )

        assert gone == ["j1"]
        assert fetched == []
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("no longer active" in r.message.lower() for r in infos)

    def test_fetch_enrichments_non_404_status_still_warns(self, caplog):
        """A 500 (or other non-404 HTTP error) still logs WARNING — only
        404 is the 'listing gone' signal."""
        import logging
        fetcher = StubFetcher()
        fetcher.enrich_delay = 0
        fetcher.max_retries = 0

        response_500 = MagicMock()
        response_500.status_code = 500
        err = httpx.HTTPStatusError("server error", request=MagicMock(), response=response_500)
        fetcher.fetch_enrichment = MagicMock(side_effect=err)

        gone = []
        with caplog.at_level(logging.WARNING, logger="jobbuddy.fetchers.base"):
            fetcher.fetch_enrichments(["j1"], on_gone=lambda jid: gone.append(jid))

        assert gone == []
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_fetch_enrichments_skips_on_fetched_for_empty_payload(self):
        """A detail page that yields no extractable fields returns an
        empty dict; on_fetched must not fire so update_enrichment isn't
        called with a no-op write."""
        fetcher = StubFetcher()
        fetcher.enrich_delay = 0
        fetcher.fetch_enrichment = MagicMock(return_value={})

        calls = []
        fetcher.fetch_enrichments(
            ["j1"],
            on_fetched=lambda jid, p: calls.append((jid, p)),
        )
        assert calls == []


# ---------------------------------------------------------------------------
# Eightfold page retry
# ---------------------------------------------------------------------------


class TestEightfoldPageRetry:
    @patch("jobbuddy.fetchers.eightfold.EightfoldFetcher._require_config")
    def test_page_fetch_retries_on_timeout(self, mock_config):
        """Eightfold list_jobs retries page fetches on timeout."""
        from jobbuddy.fetchers.eightfold import EightfoldFetcher

        fetcher = EightfoldFetcher("test", base_url="https://test.com", domain="test.com")
        mock_client = MagicMock()

        # Page 0: returns 15 total, 10 jobs
        page0_response = MagicMock()
        page0_response.json.return_value = {
            "data": {
                "count": 15,
                "positions": [{"id": str(i), "name": f"Job {i}"} for i in range(10)],
            }
        }

        # Page 1: first call times out, second succeeds
        page1_timeout = httpx.ReadTimeout("timed out")
        page1_response = MagicMock()
        page1_response.json.return_value = {
            "data": {
                "positions": [{"id": str(i), "name": f"Job {i}"} for i in range(10, 15)],
            }
        }

        mock_client.get.side_effect = [page0_response, page1_timeout, page1_response]
        fetcher.client = mock_client

        retry_events = []
        jobs = fetcher.list_jobs(
            on_retry=lambda a, ma, w, r: retry_events.append((a, ma, w, r)),
        )

        assert len(jobs) == 15
        assert len(retry_events) == 1

    @patch("jobbuddy.fetchers.eightfold.EightfoldFetcher._require_config")
    def test_page_fetch_retries_on_429(self, mock_config):
        """Eightfold list_jobs retries page fetches on 429."""
        from jobbuddy.fetchers.eightfold import EightfoldFetcher

        fetcher = EightfoldFetcher("test", base_url="https://test.com", domain="test.com")
        fetcher.backoff_base = 0.01
        mock_client = MagicMock()

        # Page 0: returns 15 total, 10 jobs
        page0_response = MagicMock()
        page0_response.json.return_value = {
            "data": {
                "count": 15,
                "positions": [{"id": str(i), "name": f"Job {i}"} for i in range(10)],
            }
        }
        page0_response.raise_for_status = MagicMock()

        # Page 1: 429 then success
        response_429 = MagicMock()
        response_429.status_code = 429
        response_429.headers = {}
        page1_err = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=response_429)

        page1_response = MagicMock()
        page1_response.json.return_value = {
            "data": {
                "positions": [{"id": str(i), "name": f"Job {i}"} for i in range(10, 15)],
            }
        }
        page1_response.raise_for_status = MagicMock()

        mock_client.get.side_effect = [page0_response, page1_err, page1_response]
        fetcher.client = mock_client

        jobs = fetcher.list_jobs()
        assert len(jobs) == 15
