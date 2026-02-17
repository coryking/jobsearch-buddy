"""Tests for sync orchestration in ats.core."""

from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.core import SyncResult, sync_jobs
from jobbuddy.models import Company, Job


def _make_job(id: str, title: str = "PM") -> Job:
    return Job(
        id=id,
        title=title,
        location="Seattle",
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
    )


def _make_company(slug: str, ats: str = "greenhouse") -> Company:
    return Company(slug=slug, name=slug.title(), ats=ats, board=slug)


class TestSync:
    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_sync_single_company(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Sync one company populates cache."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1"), _make_job("2")]
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            results = sync_jobs(company_slug="acme", db_path=str(db))

        assert len(results) == 1
        assert results[0].ok
        assert results[0].job_count == 2
        assert results[0].slug == "acme"

        # Verify data in the DB
        from jobbuddy.cache import get_connection, job_count, query_jobs
        conn = get_connection(str(db))
        assert job_count(conn) == 2
        rows = query_jobs(conn, company_slug="acme")
        assert len(rows) == 2
        conn.close()

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_sync_error_isolation(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """One company failing doesn't stop others."""
        good_co = _make_company("good")
        bad_co = _make_company("bad")
        mock_list_companies.return_value = {"good": good_co, "bad": bad_co}

        def side_effect(company):
            fetcher = MagicMock()
            if company.slug == "bad":
                fetcher.list_jobs.side_effect = Exception("Connection refused")
            else:
                fetcher.list_jobs.return_value = [_make_job("1")]
            return fetcher

        mock_get_fetcher.side_effect = side_effect

        db = tmp_path / "test.db"
        results = sync_jobs(max_workers=1, db_path=str(db))

        ok_results = [r for r in results if r.ok]
        err_results = [r for r in results if not r.ok]
        assert len(ok_results) == 1
        assert len(err_results) == 1
        assert err_results[0].error == "Connection refused"

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_sync_callbacks(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """on_result callback fires for each company."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        callback_results = []
        results = sync_jobs(
            on_result=lambda sr: callback_results.append(sr),
            db_path=str(db),
        )
        assert len(callback_results) == 1
        assert callback_results[0].ok

    def test_sync_unknown_company_raises(self):
        """Syncing a non-existent company raises ValueError."""
        with patch("jobbuddy.core.lookup_by_name", return_value=None):
            with pytest.raises(ValueError, match="Unknown company"):
                sync_jobs(company_slug="nonexistent")

    def test_sync_no_ats_raises(self):
        """Syncing a company without ATS config raises ValueError."""
        company = Company(slug="noats", name="No ATS", ats=None, board=None)
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            with pytest.raises(ValueError, match="No ATS configured"):
                sync_jobs(company_slug="noats")

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_sync_stale_skips_recent(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """--stale flag skips recently synced companies."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        # First sync
        sync_jobs(db_path=str(db))
        # Second sync with stale_hours=24 should skip
        skipped = []
        results = sync_jobs(
            stale_hours=24,
            on_skip=lambda slug, reason: skipped.append(slug),
            db_path=str(db),
        )
        assert len(results) == 0
        assert "acme" in skipped


class TestEnrichment:
    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_enrichment_fetches_descriptions_for_stub_fetchers(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Jobs from fetchers with descriptions_in_listing=False get descriptions after sync."""
        company = _make_company("workday-co", ats="workday")
        mock_list_companies.return_value = {"workday-co": company}

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False
        # list_jobs returns jobs without descriptions (stub)
        mock_fetcher.list_jobs.return_value = [_make_job("1", "PM"), _make_job("2", "SWE")]
        mock_fetcher.fetch_descriptions.return_value = {"1": "PM description", "2": "SWE description"}
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            results = sync_jobs(company_slug="workday-co", db_path=str(db))

        assert len(results) == 1
        assert results[0].ok

        from jobbuddy.cache import get_connection
        conn = get_connection(str(db))
        row1 = conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        row2 = conn.execute("SELECT description FROM jobs WHERE job_id = '2'").fetchone()
        assert row1["description"] == "PM description"
        assert row2["description"] == "SWE description"
        conn.close()

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_no_enrichment_for_full_fetchers(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Fetchers with descriptions_in_listing=True don't trigger enrichment."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = True
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            sync_jobs(company_slug="acme", db_path=str(db))

        mock_fetcher.fetch_descriptions.assert_not_called()

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_enrichment_skips_already_described_jobs(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Second sync doesn't re-enrich jobs that already have descriptions."""
        company = _make_company("workday-co", ats="workday")
        mock_list_companies.return_value = {"workday-co": company}

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_fetcher.fetch_descriptions.return_value = {"1": "description"}
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            sync_jobs(company_slug="workday-co", db_path=str(db))

        mock_fetcher.fetch_descriptions.reset_mock()

        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            sync_jobs(company_slug="workday-co", db_path=str(db))

        # Should not call fetch_descriptions since all jobs already have descriptions
        mock_fetcher.fetch_descriptions.assert_not_called()

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_enrichment_failure_isolated(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Enrichment failure doesn't break the sync pipeline."""
        company = _make_company("workday-co", ats="workday")
        mock_list_companies.return_value = {"workday-co": company}

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_fetcher.fetch_descriptions.side_effect = Exception("Network error")
        mock_get_fetcher.return_value = mock_fetcher

        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            results = sync_jobs(company_slug="workday-co", db_path=str(db))

        # Sync itself should still succeed (jobs cached, enrichment failed gracefully)
        assert len(results) == 1
        assert results[0].ok

    @patch("jobbuddy.core.list_companies")
    @patch("jobbuddy.core.get_fetcher")
    def test_enrichment_callbacks_fire(self, mock_get_fetcher, mock_list_companies, tmp_path):
        """Enrichment callbacks fire when enrichment runs."""
        company = _make_company("workday-co", ats="workday")
        mock_list_companies.return_value = {"workday-co": company}

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False
        mock_fetcher.list_jobs.return_value = [_make_job("1"), _make_job("2")]
        mock_fetcher.fetch_descriptions.return_value = {"1": "desc1", "2": "desc2"}
        mock_get_fetcher.return_value = mock_fetcher

        enrich_events = []
        db = tmp_path / "test.db"
        with patch("jobbuddy.core.lookup_by_name", return_value=company):
            sync_jobs(
                company_slug="workday-co",
                db_path=str(db),
                on_enrich_start=lambda total: enrich_events.append(("start", total)),
                on_enrich_progress=lambda done, total: enrich_events.append(("progress", done, total)),
                on_enrich_done=lambda: enrich_events.append(("done",)),
            )

        assert ("start", 2) in enrich_events
        assert ("done",) in enrich_events
        # At least one progress callback
        progress_events = [e for e in enrich_events if e[0] == "progress"]
        assert len(progress_events) >= 1


class TestSyncResult:
    def test_ok_result(self):
        sr = SyncResult("acme", job_count=10)
        assert sr.ok is True
        assert sr.error is None

    def test_error_result(self):
        sr = SyncResult("acme", error="timeout")
        assert sr.ok is False
        assert sr.error == "timeout"
